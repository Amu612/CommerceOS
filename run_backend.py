import sys
import os
import uvicorn

# Ensure backend directory is in python path
backend_dir = os.path.abspath("backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

if __name__ == "__main__":
    print("================================================================")
    print(" Starting Autonomous Orders Agent Backend on http://127.0.0.1:8000")
    print(" Interactive Documentation: http://127.0.0.1:8000/docs")
    print("================================================================")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, app_dir="backend")
