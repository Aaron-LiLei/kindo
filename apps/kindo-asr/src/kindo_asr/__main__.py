"""kindo-asr 服务入口：python -m kindo_asr.service"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("kindo_asr.service:app", host="0.0.0.0", port=8081, log_config=None)
