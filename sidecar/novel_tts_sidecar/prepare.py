"""Explicit, resumable preparation of a pinned model. The desktop never downloads weights."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .models import MODELS


def verify(path,algorithm):
    with path.open("rb") as stream:
        if algorithm=="sha256":
            return hashlib.file_digest(stream,"sha256").hexdigest()
        digest=hashlib.sha1(usedforsecurity=False)
        digest.update(f"blob {path.stat().st_size}\0".encode())
        for data in iter(lambda:stream.read(1048576),b""):
            digest.update(data)
        return digest.hexdigest()


def main():
    from huggingface_hub import HfApi,snapshot_download
    cli=argparse.ArgumentParser()
    cli.add_argument("--model",choices=list(MODELS),required=True)
    cli.add_argument("--root",type=Path,required=True)
    args=cli.parse_args()
    root=args.root.resolve()
    root.mkdir(parents=True,exist_ok=True)
    repo,revision=MODELS[args.model]
    info=HfApi().model_info(repo,revision=revision,files_metadata=True)
    if info.sha!=revision:
        raise ValueError("模型修订不匹配")
    folder=Path(snapshot_download(repo,revision=revision,local_dir=root/(args.model+"-"+revision[:8])))
    files={}
    for entry in info.siblings:
        algorithm="sha256" if entry.lfs else "git_blob_sha1"
        expected=entry.lfs.sha256 if entry.lfs else entry.blob_id
        path=folder/entry.rfilename
        if not expected or verify(path,algorithm)!=expected:
            raise ValueError("文件校验失败："+entry.rfilename)
        files[entry.rfilename]={"algorithm":algorithm,"digest":expected}
    manifest=folder/"novel-verified.json"
    manifest.write_text(json.dumps({"repo":repo,"revision":revision,"files":files},indent=2),encoding="utf-8")
    cfg=root/"runtime.json"
    settings=json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {
        "models":{},"dtype":"bfloat16","attention":"sdpa","tf32":True,"benchmark":True,"warmup":2,
        "token_ceiling":1536,"characters_per_second":3.0,"length_margin":1.55}
    settings["models"][repo]={"path":str(folder),"revision":revision}
    with NamedTemporaryFile(mode="w",encoding="utf-8",dir=root,delete=False) as f:
        json.dump(settings,f,ensure_ascii=False,indent=2)
    os.replace(f.name,cfg)
    print(cfg)


if __name__=="__main__":
    main()
