"""Download natural Piper voices for JARVIS, A.T.O.M and R.U.F.U.S (one male voice each).

Run once, with your venv active:   python get_voices.py
Voices come from the official Piper voice library and work offline afterwards.
"""
from jarvis.config import Config
from jarvis.core.voices import RECOMMENDED, download_recommended

if __name__ == "__main__":
    for persona, (code, desc) in RECOMMENDED.items():
        print(f"  {persona.upper():7} {code}  –  {desc}")
    download_recommended(Config.load(), progress=print)
    print("JARVIS is now set to use these voices. Start it with:  python main.py")
