# 在 M1 上跑,不在 Air 的容器裡跑(要 server 的 .venv 與 FlagEmbedding);檔案 scp 到 ~/bge-m3-server/ 執行。
# M1 記憶體探針:server 為什麼長到 38 GB,每次請求後清快取有沒有用(手冊 §13)。
#
# 不經過 HTTP,直接用 server.py 同一種方式載入模型、同一組 encode 參數,
# 依序跑一串長短不同的請求,每次之後記:耗時、PyTorch 已配置 / 向驅動要的 GPU 記憶體、
# 行程 phys_footprint。
#
# 跑法(在 M1,server 要先停):
#     cd ~/bge-m3-server && PYTORCH_ENABLE_MPS_FALLBACK=1 uv run python mem_probe.py baseline
#     cd ~/bge-m3-server && PYTORCH_ENABLE_MPS_FALLBACK=1 uv run python mem_probe.py empty_cache

import json
import os
import re
import subprocess
import sys
import time

import torch
from FlagEmbedding import BGEM3FlagModel
from huggingface_hub import snapshot_download

MODE = sys.argv[1]  # baseline | empty_cache
GB = 1024**3

path = snapshot_download(
    "BAAI/bge-m3",
    revision="5617a9f61b028005a4858fdac845db406aefb181",
    local_files_only=True,
    ignore_patterns=["onnx/*", "imgs/*", "*.jpg", "*.webp"],
)
model = BGEM3FlagModel(path, use_fp16=False, devices="mps")
T = json.load(open(os.path.expanduser("~/bge-m3-server/mem_texts.json")))

# 模仿作業 2-1 的尾段:長段、中段交錯,最後回到短段,看記憶體會不會回落
PLAN = [
    ("mid4k x16", T["mid4k"][:16]),
    ("long8k x4", T["long8k"][:4]),
    ("mid4k x16", T["mid4k"][16:32]),
    ("long8k x4", T["long8k"][4:8]),
    ("long8k x16", T["long8k"][:16]),
    ("short x128", T["short"]),
]


def footprint_gb() -> float:
    out = subprocess.run(["footprint", str(os.getpid())], capture_output=True, text=True).stdout
    m = re.search(r"phys_footprint:\s*([\d.]+)\s*(GB|MB|KB)", out)
    if not m:
        return -1.0
    return float(m[1]) * {"GB": 1, "MB": 1 / 1024, "KB": 1 / 1024**2}[m[2]]


def snap(label: str, **kw) -> None:
    rec = {"mode": MODE, "step": label,
           "allocated_gb": round(torch.mps.current_allocated_memory() / GB, 2),
           "driver_gb": round(torch.mps.driver_allocated_memory() / GB, 2),
           "footprint_gb": round(footprint_gb(), 2), **kw}
    print(json.dumps(rec), flush=True)


snap("loaded")
for label, texts in PLAN:
    tok = sum(len(model.tokenizer(t)["input_ids"]) for t in texts)
    t0 = time.time()
    model.encode(texts, batch_size=16, max_length=8192,
                 return_dense=True, return_sparse=False, return_colbert_vecs=False)
    dt = time.time() - t0
    snap(label, tokens=tok, seconds=round(dt, 1), tok_per_s=round(tok / dt))
    if MODE == "empty_cache":
        t1 = time.time()
        torch.mps.empty_cache()
        snap(label + " +empty_cache", empty_cache_seconds=round(time.time() - t1, 3))
