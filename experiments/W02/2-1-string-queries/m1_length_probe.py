"""在 M1 上跑,不在 Air 的容器裡跑(要 server 的 .venv 與 FlagEmbedding);檔案 scp 到 ~/bge-m3-server/ 執行。
M1 段長 vs 速度(手冊 §13.2):不經 HTTP,每次 encode 後清快取,段長 256/512/1024/2048/4096/8000 各 3 次。

分批照作業 2-1:一次至多 128 段且至多 30 萬字元;每次約 12.8 萬 token。
k8s_objects 文字每 token 約 2.562 字元(手冊 §13 校準)。
跑法:cd ~/bge-m3-server && PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python length_probe.py
"""
import json, os, time
import torch
from FlagEmbedding import BGEM3FlagModel
from huggingface_hub import snapshot_download

path = snapshot_download("BAAI/bge-m3", revision="5617a9f61b028005a4858fdac845db406aefb181",
                         local_files_only=True, ignore_patterns=["onnx/*", "imgs/*", "*.jpg", "*.webp"])
model = BGEM3FlagModel(path, use_fp16=False, devices="mps")
T = json.load(open(os.path.expanduser("~/bge-m3-server/mem_texts.json")))
text = "\n".join(T["long8k"] + T["mid4k"])
for target in [256, 512, 1024, 2048, 4096, 8000]:
    c = int(target * 2.562)
    n_seg = max(1, min(128, 300_000 // c))
    n_req = max(1, round(128_000 / (n_seg * target)))
    segs = [text[i * c:(i + 1) * c] for i in range(n_seg * n_req)]
    assert len(segs[-1]) == c
    for rep in range(3):
        tok = sec = 0
        for r in range(n_req):
            batch = segs[r * n_seg:(r + 1) * n_seg]
            tok += sum(len(model.tokenizer(t)["input_ids"]) for t in batch)
            t0 = time.time()
            model.encode(batch, batch_size=16, max_length=8192, return_dense=True,
                         return_sparse=False, return_colbert_vecs=False)
            sec += time.time() - t0
            torch.mps.empty_cache()
        print(json.dumps({"target": target, "rep": rep, "segs_per_req": n_seg, "requests": n_req,
                          "avg_tok_per_seg": round(tok / (n_seg * n_req)), "tokens": tok,
                          "seconds": round(sec, 1), "tok_per_s": round(tok / sec)}), flush=True)
