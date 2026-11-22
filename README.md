# discord-call-speech-to-text

Transcreve uma chamada do Discord no Linux e grava um log de texto enquanto a conversa acontece.

O programa escuta duas fontes, sem entrar na chamada como bot:

- `eu` — o microfone que o Discord está usando
- `discord` — o áudio da chamada que você está ouvindo

As outras pessoas ficam juntas na linha `discord`. O texto é gerado localmente com [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Requisitos

- Linux com PulseAudio ou PipeWire (`parecord` e `pactl`)
- Python 3.10 ou mais recente
- GPU NVIDIA é opcional. Sem as bibliotecas CUDA, a transcrição cai para a CPU.

## Instalação

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Na primeira execução o modelo `small` é baixado. Para usar a GPU, `libcublas.so.12` precisa estar visível. O programa procura essa biblioteca no ambiente conda ativo, nos ambientes do Anaconda/Miniconda e em instalações comuns da CUDA. Também dá para indicar o diretório diretamente:

```bash
export LD_LIBRARY_PATH="/caminho/para/lib:$LD_LIBRARY_PATH"
```

## Uso

Entre na chamada e inicie:

```bash
./iniciar.sh
```

Acompanhe o log:

```bash
tail -f logs/atual.txt
```

Pare com:

```bash
./parar.sh
```

Cada execução cria um arquivo `logs/chamada-AAAA-MM-DD_HH-MM-SS.txt`. `logs/atual.txt` aponta para o arquivo corrente.

Opções do transcritor:

```bash
.venv/bin/python transcrever.py --help
```

`--model` aceita `tiny`, `base`, `small` ou `medium`. `--lang` aceita um código como `pt` ou `auto`.
