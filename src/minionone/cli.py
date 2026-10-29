"""Operator diagnostics only; NEVER worker launch, unrestricted IO or MCP host."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from . import __version__
from .lmstudio_probe import LMStudioProbe
from .lmstudio_completion import LMStudioCompletion

_BASE='http://127.0.0.1:1234'
_SYNTHETIC_PROMPT='Respond to this harmless check: MINIONONE_SMOKE_2026. No project files are provided.'


def _print(obj):
    print(json.dumps(obj,sort_keys=True,ensure_ascii=True))


def main(argv=None):
    parser=argparse.ArgumentParser(prog='minionone',description='Pre-alpha local diagnostics (not a job manager)')
    subs=parser.add_subparsers(dest='command',required=True)
    subs.add_parser('status',help='Report actual implemented security gates (offline)')
    probe=subs.add_parser('probe-lmstudio',help='Read-only local model discovery')
    probe.add_argument('--base-url',default=_BASE)
    probe.add_argument('--timeout',type=float,default=2)
    smoke=subs.add_parser('smoke',help='Send a fixed synthetic prompt, never project data')
    smoke.add_argument('--model',required=True)
    smoke.add_argument('--base-url',default=_BASE)
    smoke.add_argument('--timeout',type=float,default=120)
    args=parser.parse_args(argv)
    if args.command=='status':
        _print({'version':__version__,'phase':'pre-alpha','release_gate':'NO_GO',
                'mcp_server_enabled':False,'worker_tools_enabled':False,
                'windows_broker_verified':False,'same_chat_wakeup_verified':False,
                'provider_live_verified':False})
        return 0
    try:
        key=os.environ.get('MINIONONE_LMSTUDIO_KEY')
        if args.command=='probe-lmstudio':
            models=LMStudioProbe(args.base_url,timeout=args.timeout,api_key=key).models()
            _print({'ok':True,'models':models,'live_provider_identity_verified':False})
            return 0
        if args.command=='smoke':
            result=LMStudioCompletion(args.base_url,timeout=args.timeout,api_key=key).complete(
                args.model,_SYNTHETIC_PROMPT,max_tokens=128)
            content=result['text']
            _print({'responded':True,'worker_tools_enabled':False,'result_trusted':False,
                    'response_bytes':len(content.encode('utf-8')),
                    'response_sha256':hashlib.sha256(content.encode('utf-8')).hexdigest()})
            return 0
    except Exception:
        # Never reflect provider/model errors, secrets, URLs or raw generated text.
        _print({'ok':False,'responded':False,'error_code':'LOCAL_PROBE_FAILED'})
        return 2
    return 2
