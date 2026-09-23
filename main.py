import uvicorn, webbrowser, threading, time

def start():
    uvicorn.run("backend.app:app", host="127.0.0.1", port=4040, log_level="error")

if __name__ == "__main__":
    t = threading.Thread(target=start, daemon=True)
    t.start()
    time.sleep(1)
    webbrowser.open("http://127.0.0.1:4040")
    print("Empyrion Playfield Debugger Tool running at http://localhost:4040")
    print("Press CTRL+C to stop.")
    t.join()
