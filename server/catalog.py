"""Model families and converted weight identities; runtime revisions are recorded separately."""
from pathlib import Path

MODEL_CATALOG = [
    dict(id='tiny',family='Whisper tiny',parameters_m=39,repository='Systran/faster-whisper-tiny',quality='Exploração rápida',use='Testes leves e áudio claro',vram_hint='~1 GB'),
    dict(id='base',family='Whisper base',parameters_m=74,repository='Systran/faster-whisper-base',quality='Leve',use='Baixo custo de memória e CPU',vram_hint='~1 GB'),
    dict(id='small',family='Whisper small',parameters_m=244,repository='Systran/faster-whisper-small',quality='Equilibrado',use='Ponto inicial para comparar velocidade e qualidade',vram_hint='~2 GB'),
    dict(id='medium',family='Whisper medium',parameters_m=769,repository='Systran/faster-whisper-medium',quality='Maior capacidade',use='Áudio mais difícil; maior custo de execução',vram_hint='~5 GB'),
    dict(id='large-v3',family='Whisper large · v3',parameters_m=1550,repository='Systran/faster-whisper-large-v3',quality='Maior capacidade',use='Comparações de qualidade multilíngue',vram_hint='~10 GB'),
    dict(id='turbo',family='Whisper large · v3 turbo',parameters_m=809,repository='mobiuslabsgmbh/faster-whisper-large-v3-turbo',quality='Otimizado para velocidade',use='Transcrição rápida com a família large-v3',vram_hint='~6 GB'),
]

def installed_models(root):
    result=[]
    for model in MODEL_CATALOG:
        path=Path(root)/('models--'+model['repository'].replace('/','--'))
        ref=path/'refs'/'main'
        revision=ref.read_text().strip() if ref.exists() else None
        snapshot=path/'snapshots'/str(revision)
        ready=revision is not None and (snapshot/'model.bin').is_file()
        result.append({**model,'installed':ready,'revision':revision if ready else None,'multilingual':True,
                       'source':'https://huggingface.co/'+model['repository'],
                       'memory_note':'Referência aproximada do Whisper original; precisão, motor e lote alteram o consumo. Não é previsão de VRAM deste executor.'})
    return result
