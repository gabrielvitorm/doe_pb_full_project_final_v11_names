# automation/doe_pb_core.py
from __future__ import annotations
import os, re, io, csv, time, unicodedata, urllib.request, urllib.parse
from pathlib import Path
from dataclasses import dataclass
import pdfplumber

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE_URL = "https://auniao.pb.gov.br/doe/edicoes-recentes"
CUTOFF_YEAR_STOP = 2019
PAGE_STEP = 12

def nrm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")

RE_DATE = re.compile(r"(\d{2})[-/](\d{2})[-/](\d{4})")
RE_NOME_CAIXA = re.compile(r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}){1,})\b")
RE_MATRICULA = re.compile(r"(?i)matricul[ao]?(?:\s*n[ºo°\.\-:]*)?\s*[:\-]?\s*([A-Z0-9./\-]+)")
RE_PROCESSO = re.compile(r"(?i)processo(?:\s*n[ºo°\.\-:]*)?\s*[:\-]?\s*([A-Z0-9./\-]+)")

# Nome após "a/ao/à" (permite 'vitalícia/temporária' e frases intermediárias)
RE_NOME_POS_FRASE = re.compile(
    r"(?i)conceder\s+(?:aposentadori[ao]|pens[aã]o)\s+(?:vital[ií]cia|tempor[áa]ria)?\s*(?:,?\s*por[^,.;]+)?\s*a[oà]?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s\-.]{6,}?)(?:,|\.|;|$)"
)
# Nome após papéis
RE_NOME_SERVIDOR = re.compile(
    r"(?i)(?:servidor(?:a)?|benefici[áa]ri[ao]|pensionist[ao]|requerente)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s\-.]{6,})"
)

BAD_TOKENS = set("""APOSENTADORIA PENSÃO PENSAO PORTARIA GABINETE PRESIDÊNCIA PRESIDENCIA ESTADO PARAÍBA PARAIBA
DO DA DE E O A DIÁRIO OFICIAL GOVERNO PBPREV SECRETARIA RESOLVE CONCEDER VOLUNTÁRIA COMPULSÓRIA COMPULSORIA""".split())

try:
    from .browser_config import build_driver  # type: ignore
except Exception:
    from automation.browser_config import build_driver  # type: ignore

@dataclass
class Registro:
    data_link: str
    detail_url: str
    pdf_url: str
    tipo_ato: str
    nome: str
    matricula: str
    processo: str
    pagina: int
    trecho: str

class DoePBScraper:
    def __init__(self, download_dir: Path | str = "downloads_doe_pb", headless: bool = True, timeout: int = 60):
        self.base_url = BASE_URL
        self.download_dir = Path(download_dir); self.download_dir.mkdir(exist_ok=True, parents=True)
        self.headless = headless
        self.timeout = timeout
        self.driver = None
        self.wait = None

    def start(self):
        self.driver = build_driver(headless=self.headless, download_path=str(self.download_dir))
        self.wait = WebDriverWait(self.driver, self.timeout)

    def stop(self):
        try:
            if self.driver: self.driver.quit()
        finally:
            self.driver = None; self.wait = None

    def _dismiss_overlays(self):
        try:
            self.driver.execute_script("const el = document.getElementById('viewlet-disclaimer'); if (el) el.remove();")
        except Exception:
            pass

    # --- helpers ---
    def _download_pdf_bytes(self, url: str, referer: str | None = None) -> bytes | None:
        headers = {"User-Agent":"Mozilla/5.0", "Accept":"application/pdf,*/*;q=0.8"}
        if referer: headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                ct = resp.headers.get("Content-Type","")
                data = resp.read()
                if data[:4] == b"%PDF" or "application/pdf" in ct:
                    return data
        except Exception:
            return None
        return None

    def _find_pdf_link_in_detail(self, detail_url: str):
        d = self.driver; w = self.wait; assert d and w
        d.get(detail_url)
        try: w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href]")))
        except Exception: pass
        self._dismiss_overlays()

        base = d.current_url
        candidates = []

        for a in d.find_elements(By.CSS_SELECTOR, "a[href]"):
            href = a.get_attribute("href") or ""
            if href.lower().endswith(".pdf") or "@@download/file" in href:
                candidates.append(urllib.parse.urljoin(base, href))

        for sel in ["iframe[src]", "embed[src]"]:
            for el in d.find_elements(By.CSS_SELECTOR, sel):
                src = el.get_attribute("src") or ""
                if ".pdf" in src or "@@download/file" in src:
                    candidates.append(urllib.parse.urljoin(base, src))

        if detail_url.lower().endswith(".pdf") and "/@@download/file" not in detail_url:
            candidates.append(detail_url.rstrip("/") + "/@@download/file")
        if not candidates:
            candidates.append(detail_url)

        seen = set()
        for u in candidates:
            if u in seen: continue
            seen.add(u)
            data = self._download_pdf_bytes(u, referer=detail_url)
            if data:
                return data, u

        html = d.page_source
        for m in re.finditer(r'href=["\\\']([^"\\\']+\\.pdf[^"\\\']*)["\\\']', html, flags=re.I):
            u = urllib.parse.urljoin(base, m.group(1))
            data = self._download_pdf_bytes(u, referer=detail_url)
            if data:
                return data, u

        return None, (candidates[0] if candidates else detail_url)

    # --- extraction ---
    def _extract_records(self, pdf_bytes: bytes):
        registros = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for i, pg in enumerate(pdf.pages, start=1):
                txt = pg.extract_text() or ""
                if not txt:
                    continue
                raw_blocks = [b for b in txt.split("\n\n") if b.strip()]
                if len(raw_blocks) < 3:
                    raw_blocks = [b for b in txt.split("\n") if b.strip()]
                for bloco in raw_blocks:
                    bflat = re.sub(r"\s+", " ", bloco).strip()
                    nb = nrm(bflat)
                    tipo = None
                    if "conceder aposentadoria" in nb:
                        tipo = "APOSENTADORIA"
                    elif "conceder pensao" in nb or "conceder pensão" in bflat.lower():
                        tipo = "PENSAO"
                    elif ("isencao de imposto de renda" in nb or "isenção de imposto de renda" in bflat.lower()) and ("indef" in nb):
                        tipo = "ISENCAO_INDEFERIDA"
                    if not tipo:
                        continue

                    nome = ""
                    m = RE_NOME_POS_FRASE.search(bflat)
                    if m:
                        nome = m.group(1).strip(" .;-:,")
                    if not nome:
                        m2 = RE_NOME_SERVIDOR.search(bflat)
                        if m2:
                            nome = m2.group(1).strip(" .;-:,")
                    if not nome:
                        win = bflat
                        km = re.search(r"(?i)matricul|processo|benefici[áa]ri[ao]|pensionist[ao]", bflat)
                        if km:
                            start = max(0, km.start()-120); end = min(len(bflat), km.end()+120)
                            win = bflat[start:end]
                        nomes = RE_NOME_CAIXA.findall(win)
                        cand = []
                        for nm in nomes:
                            parts = nm.split()
                            if len(parts) < 2: continue
                            if any(p.upper() in BAD_TOKENS for p in parts): continue
                            cand.append(nm.strip())
                        if cand:
                            cand.sort(key=lambda x: (-len(x), not bool(re.search(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}\b", x))))
                            nome = cand[0]

                    mmat = RE_MATRICULA.search(bflat)
                    matricula = (mmat.group(1).strip(".,; ") if mmat else "")
                    mpro = RE_PROCESSO.search(bflat)
                    processo  = (mpro.group(1).strip(".,; ") if mpro else "")

                    registros.append({
                        "tipo_ato": tipo,
                        "nome": nome,
                        "matricula": matricula,
                        "processo": processo,
                        "pagina": i,
                        "trecho": bflat[:260]
                    })
        return registros

    # --- streaming histórico ---
    def _iter_listing_page(self, url):
        d = self.driver; assert d
        d.get(url)
        time.sleep(0.5); self._dismiss_overlays()
        items = []; stop_flag = False
        for a in d.find_elements(By.CSS_SELECTOR, "a[href]"):
            txt = (a.text or "").strip()
            if "Diário Oficial" not in txt or ".pdf" not in txt: 
                continue
            href = a.get_attribute("href") or ""
            m = RE_DATE.search(txt); year = int(m.group(3)) if m else None
            if year is not None and year <= CUTOFF_YEAR_STOP:
                stop_flag = True; continue
            items.append((href, txt, year))
        return stop_flag, items

    def _historico_stream(self, saida_csv: str):
        seen = set(); saved = 0; rows_total = 0
        with open(saida_csv, "w", newline="", encoding="utf-8") as f:
            header = ["data_link","detail_url","pdf_url","tipo_ato","nome","matricula","processo","pagina","trecho"]
            w = csv.DictWriter(f, fieldnames=header); w.writeheader()

            stop, items = self._iter_listing_page(self.base_url)
            print(f"[PAG 0] itens: {len(items)}  stop={stop}")
            for href, txt, year in items:
                if href in seen or (year is not None and year < 2020): continue
                seen.add(href)
                pdf_bytes, pdf_url = self._find_pdf_link_in_detail(href)
                if not pdf_bytes: print(f"[SKIP] sem PDF: {href}"); continue
                saved += 1
                fname = self.download_dir / (txt.replace(" ", "_").replace("/", "-"))
                if not str(fname).lower().endswith(".pdf"): fname = fname.with_suffix(".pdf")
                try: open(fname, "wb").write(pdf_bytes)
                except Exception: pass
                for r in self._extract_records(pdf_bytes):
                    w.writerow({ "data_link": txt, "detail_url": href, "pdf_url": pdf_url or "", **r }); rows_total += 1
                print(f"[OK] PDF {saved} salvo | registros total: {rows_total}")

            if stop: print(f"[DONE] PDFs: {saved} | registros: {rows_total}"); return os.path.abspath(saida_csv)

            page_idx = 1
            for start in range(12, 20000, PAGE_STEP):
                url = f"{self.base_url}?b_start:int={start}"
                stop, items = self._iter_listing_page(url)
                print(f"[PAG {page_idx}] itens: {len(items)}  stop={stop}")
                for href, txt, year in items:
                    if href in seen or (year is not None and year < 2020): continue
                    seen.add(href)
                    pdf_bytes, pdf_url = self._find_pdf_link_in_detail(href)
                    if not pdf_bytes: print(f"[SKIP] sem PDF: {href}"); continue
                    saved += 1
                    fname = self.download_dir / (txt.replace(" ", "_").replace("/", "-"))
                    if not str(fname).lower().endswith(".pdf"): fname = fname.with_suffix(".pdf")
                    try: open(fname, "wb").write(pdf_bytes)
                    except Exception: pass
                    for r in self._extract_records(pdf_bytes):
                        w.writerow({ "data_link": txt, "detail_url": href, "pdf_url": pdf_url or "", **r }); rows_total += 1
                    print(f"[OK] PDF {saved} salvo | registros total: {rows_total}")
                if stop: break; page_idx += 1
        print(f"[DONE] PDFs: {saved} | registros: {rows_total}")
        return os.path.abspath(saida_csv)

    def rodar(self, modo: str = "historico", saida_csv: str = "resultados_doe_pb.csv") -> str:
        assert modo in ("historico","diario")
        self.start()
        try:
            if modo == "historico":
                return self._historico_stream(saida_csv=saida_csv)
            else:
                d = self.driver; assert d
                d.get(self.base_url); time.sleep(0.5); self._dismiss_overlays()
                detalhes = []
                for a in d.find_elements(By.CSS_SELECTOR, "a[href]"):
                    txt = (a.text or "").strip()
                    if "Diário Oficial" in txt and ".pdf" in txt:
                        detalhes.append((a.get_attribute("href"), txt))
                detalhes = detalhes[:5]

                rows = []; saved = 0
                for detail_url, link_text in detalhes:
                    try:
                        pdf_bytes, pdf_url = self._find_pdf_link_in_detail(detail_url)
                        if not pdf_bytes: continue
                        saved += 1
                        fname = self.download_dir / (link_text.replace(" ", "_").replace("/", "-"))
                        if not str(fname).lower().endswith(".pdf"): fname = fname.with_suffix(".pdf")
                        try: open(fname, "wb").write(pdf_bytes)
                        except Exception: pass
                        for r in self._extract_records(pdf_bytes):
                            rows.append({ "data_link": link_text, "detail_url": detail_url, "pdf_url": pdf_url or "", **r })
                    except Exception:
                        continue

                header = ["data_link","detail_url","pdf_url","tipo_ato","nome","matricula","processo","pagina","trecho"]
                with open(saida_csv, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=header)
                    w.writeheader()
                    for r in rows: w.writerow(r)
                print(f"[INFO] PDFs salvos: {saved} | CSV: {os.path.abspath(saida_csv)}")
                return os.path.abspath(saida_csv)
        finally:
            self.stop()
