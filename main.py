import threading
import time
import webbrowser

import uvicorn

import backend.app as appmod


def start():
    uvicorn.run(appmod.app, host="127.0.0.1", port=4040, log_level="info")


if __name__ == "__main__":
    thread = threading.Thread(target=start, daemon=True)
    thread.start()
    time.sleep(1)
    webbrowser.open("http://127.0.0.1:4040")
    print("Empyrion Playfield Studio running at http://127.0.0.1:4040")
    print("Edit log: logs/epd-edits.log")
    print("Scan report: logs/epd-last-scan.txt")
    print("Press CTRL+C to stop.")
    thread.join()
