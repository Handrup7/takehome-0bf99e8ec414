from fastapi import FastAPI

app = FastAPI(title="Late checkout")


@app.get("/health")
def health():
    return {"status": "ok", "service": "late-checkout-app"}