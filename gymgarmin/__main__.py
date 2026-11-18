"""Inicia o Hybrid Lab:  python -m gymgarmin  [--host 0.0.0.0] [--port 8000]

Com --host 0.0.0.0 o celular na mesma rede Wi-Fi acessa http://IP-DO-COMPUTADOR:8000
"""

import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="Hybrid Lab – servidor local")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    uvicorn.run("gymgarmin.app:app", host=a.host, port=a.port)


if __name__ == "__main__":
    main()
