#!/usr/bin/env python3
import argparse
from automation.doe_pb_core import DoePBScraper

def main():
    ap = argparse.ArgumentParser(description="DOE-PB Scraper (historico/diario)")
    ap.add_argument("--modo", choices=["historico","diario"], default="diario")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--saida", default="resultados_doe_pb.csv")
    ap.add_argument("--dir", default="downloads_doe_pb")
    args = ap.parse_args()

    bot = DoePBScraper(download_dir=args.dir, headless=args.headless)
    out = bot.rodar(modo=args.modo, saida_csv=args.saida)
    print(f"[OK] CSV gerado em: {out}")

if __name__ == "__main__":
    main()
