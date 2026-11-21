"""Collect cold loads and uncached synthesis timings. Audio-device onset is measured separately."""
import argparse
import json
import math
import platform
import statistics
import subprocess
import threading
import time
from datetime import datetime,timezone
from pathlib import Path

from probe_local_tts import PROFILES,SAMPLES,arguments,session


def board():
    try:
        return subprocess.check_output(["nvidia-smi","--query-gpu=name,driver_version,memory.used,memory.total",
            "--format=csv,noheader,nounits"],text=True,timeout=3).strip()
    except (OSError,subprocess.SubprocessError):
        return None


def main():
    cli=argparse.ArgumentParser()
    arguments(cli)
    cli.add_argument("--cold-starts",type=int,default=5)
    cli.add_argument("--repeats",type=int,default=50)
    cli.add_argument("--output",type=Path,required=True)
    args=cli.parse_args()
    if min(args.repeats,args.cold_starts)<1:
        cli.error("测量次数须为正数")
    with args.output.open("x",encoding="utf-8") as out:
        def record(kind,**kw):
            row=dict(kind=kind,time=datetime.now(timezone.utc).isoformat(),**kw)
            out.write(json.dumps(row,ensure_ascii=False)+"\n")
            out.flush()
        record("environment",platform=platform.platform(),board=board(),profile=args.profile,
               device_onset="需另行回环或听测",realtime_playback="未测量")
        rtf=[]
        for cold in range(args.cold_starts):
            start=time.monotonic()
            try:
                with session(args.runtime,args.settings) as client:
                    cap=client.load(PROFILES[args.profile],threading.Event())
                    record("cold",iteration=cold,wall_seconds=time.monotonic()-start,capabilities=cap,board=board())
                    if cold!=args.cold_starts-1:
                        continue
                    for i in range(args.repeats):
                        text=(SAMPLES["narration_short"],SAMPLES["dialogue_long"],SAMPLES["dialogue_long"]*3)[i%3]
                        body=dict(model=cap["model"],voice=cap["voices"][0],text=text)
                        begin=time.monotonic()
                        job,metrics=client.job("synthesis/jobs",body,threading.Event())
                        audio=client.call("GET",f"artifacts/{job}").content
                        elapsed=time.monotonic()-begin
                        record("uncached",iteration=i,sample=i%3,wall_seconds=elapsed,bytes=len(audio),board=board(),**metrics)
                        rtf.append(metrics["rtf"])
            except Exception as exc:
                record("failure",iteration=cold,error=type(exc).__name__,detail=str(exc))
                raise
        record("summary",count=len(rtf),rtf_p50=statistics.median(rtf),rtf_p95=sorted(rtf)[math.ceil(.95*len(rtf))-1])


if __name__=="__main__":
    main()
