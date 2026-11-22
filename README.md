# discord-call-speech-to-text

Transcreve uma chamada do Discord e grava um log de texto enquanto a conversa acontece. Funciona no Linux e no Windows. O programa não entra na chamada como bot.

Duas fontes:

- `eu` — o seu microfone
- `discord` — o áudio da chamada

No Linux, `eu` é o microfone que o Discord está usando e `discord` é só o áudio do Discord. No Windows, `eu` é o microfone padrão e `discord` é o áudio do processo do Discord. Se o Windows não deixar isolar o processo, a linha `discord` passa a gravar o que sai nos alto-falantes.

As outras pessoas ficam juntas na linha `discord`. O texto é gerado localmente com [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Requisitos

- Python 3.10 ou mais recente
- Linux: PulseAudio ou PipeWire, com `parecord` e `pactl`
- Windows 10 2004 ou mais recente, para isolar o áudio do Discord
- GPU NVIDIA é opcional. Sem as bibliotecas CUDA, a transcrição usa a CPU.

## Instalação

Linux:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Windows, no PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Na primeira execução o modelo `small` é baixado.

No Linux, a GPU precisa enxergar `libcublas.so.12`. O programa procura essa biblioteca no conda ativo, nos ambientes do Anaconda/Miniconda e em instalações comuns da CUDA. Dá para indicar o diretório:

```bash
export LD_LIBRARY_PATH="/caminho/para/lib:$LD_LIBRARY_PATH"
```

No Windows, o programa procura `cublas64_12.dll` no ambiente virtual, no conda e em `CUDA_PATH`.

## Uso

Entre na chamada e inicie.

Linux:

```bash
./iniciar.sh
tail -f logs/atual.txt
./parar.sh
```

Windows:

```bat
iniciar.bat
powershell -NoProfile -Command "Get-Content -Wait logs\atual.txt"
parar.bat
```

Nos dois sistemas, este comando roda no terminal em vez de ir para segundo plano:

```bash
python transcrever.py
```

Cada execução cria `logs/chamada-AAAA-MM-DD_HH-MM-SS.txt`. `logs/atual.txt` aponta para o arquivo corrente. No Windows, se o sistema não permitir um atalho, o mesmo texto é escrito direto em `logs/atual.txt`.

O log fica assim:

```text
[15:04:12] eu: texto do microfone
[15:04:15] discord: texto de quem você está ouvindo
```

Os arquivos de log não entram no repositório.

`--model` aceita `tiny`, `base`, `small` ou `medium`. `--lang` aceita um código como `pt` ou `auto`.

```bash
python transcrever.py --help
```
