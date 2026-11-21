"""Send only the built-in public sample. Output contains no credential or novel content."""
import argparse
import json
from threading import Event

from novel_tts.domain.models import AnnotationRequest,Parent
from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.profiles import Profiles
from novel_tts.providers.llm import RemoteLLM


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--profile",choices=["openai","deepseek","custom"],required=True)
    parser.add_argument("--fixture",choices=["dialogue_short"],default="dialogue_short")
    args=parser.parse_args()
    profiles=Profiles(Paths().data)
    p=profiles.all()[args.profile]
    req=AnnotationRequest("probe","chapter","r1",0,"probe-1",(Parent("s17",0,"林渊说：“别走。”少年阿宁答：“好。”"),))
    try:
        remote=RemoteLLM(p,profiles.credential(p),record=lambda row:print(json.dumps(row,ensure_ascii=False)))
        result=remote.analyze_chunk(req,Event())
        print(json.dumps(dict(verified=True,fixture=args.fixture,model=result.model,parts=sum(len(parts) for _,parts in result.lines))))
        return 0
    except Exception as exc:
        print(json.dumps(dict(verified=False,profile=p.name,reason=str(exc)),ensure_ascii=False))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
