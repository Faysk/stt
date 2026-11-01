"""Isolated, cancellable disk-cache preparation; never retains a model in the agent."""
import json,sys
from pathlib import Path
from server.worker import prepare_model
if __name__=='__main__':prepare_model(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
