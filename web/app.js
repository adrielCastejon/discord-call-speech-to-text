const estadoEl = document.querySelector("#estado");
const estadoTexto = document.querySelector("#estado-texto");
const erroEl = document.querySelector("#erro");
const logEl = document.querySelector("#log");
const vazio = document.querySelector("#vazio");
const arquivoEl = document.querySelector("#arquivo");
const modeloDesc = document.querySelector("#modelo-desc");
const modelo = document.querySelector("#modelo");
const idioma = document.querySelector("#idioma");
const botoes = {
  iniciar: document.querySelector("#iniciar"),
  pausar: document.querySelector("#pausar"),
  continuar: document.querySelector("#continuar"),
  encerrar: document.querySelector("#encerrar"),
};

const nomes = {
  parado: "Parado",
  iniciando: "Abrindo",
  ouvindo: "Ouvindo",
  pausado: "Pausado",
};

let cursor = 0;
let ocupado = false;
let noFundo = true;

logEl.addEventListener("scroll", () => {
  noFundo = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
});

function nivel(id, rms) {
  const barra = document.querySelector(id);
  const largura = Math.max(0, Math.min(120, rms * 700));
  barra.style.width = `${largura}px`;
}

function linha(item) {
  const p = document.createElement("p");
  const hora = document.createElement("span");
  hora.className = "hora";
  hora.textContent = item.hora;
  p.append(hora);
  if (item.tipo === "nota") {
    p.className = "nota";
    const texto = document.createElement("span");
    texto.textContent = item.texto;
    p.append(texto);
    return p;
  }
  p.className = "fala";
  const rotulo = document.createElement("span");
  rotulo.className = `rotulo ${item.rotulo}`;
  rotulo.textContent = item.rotulo;
  const texto = document.createElement("span");
  texto.textContent = item.texto;
  p.append(rotulo, texto);
  return p;
}

function desenhar(items, substituir) {
  if (substituir) {
    logEl.replaceChildren();
  }
  if (!items.length) {
    if (!logEl.querySelector(".fala, .nota")) {
      if (!logEl.contains(vazio)) {
        logEl.append(vazio);
      }
      vazio.hidden = false;
    }
    return;
  }
  if (logEl.contains(vazio)) {
    vazio.remove();
  }
  for (const item of items) {
    logEl.append(linha(item));
  }
  if (noFundo) {
    logEl.scrollTop = logEl.scrollHeight;
  }
}

function aplicar(data) {
  estadoEl.className = `estado ${data.estado}`;
  estadoTexto.textContent = nomes[data.estado] || data.estado;
  const parado = data.estado === "parado";
  const ouvindo = data.estado === "ouvindo";
  const pausado = data.estado === "pausado";
  botoes.iniciar.disabled = ocupado || !parado;
  botoes.pausar.disabled = ocupado || !ouvindo;
  botoes.continuar.disabled = ocupado || !pausado;
  botoes.encerrar.disabled = ocupado || parado || data.estado === "iniciando";
  modelo.disabled = !parado;
  idioma.disabled = !parado;
  if (data.erro) {
    erroEl.hidden = false;
    erroEl.textContent = data.erro;
  } else {
    erroEl.hidden = true;
    erroEl.textContent = "";
  }
  arquivoEl.textContent = data.arquivo || "O log aparece aqui e também em logs/atual.txt";
  modeloDesc.textContent = data.modelo_desc || "";
  nivel("#nivel-eu", data.rms_eu || 0);
  nivel("#nivel-discord", data.rms_discord || 0);
  if (cursor > data.total || data.desde !== cursor) {
    desenhar(data.linhas, true);
  } else {
    desenhar(data.linhas, false);
  }
  cursor = data.total;
}

async function atualizar() {
  const resposta = await fetch(`/api/estado?desde=${cursor}`);
  if (!resposta.ok) {
    throw new Error("não consegui ler o estado");
  }
  aplicar(await resposta.json());
}

async function acao(caminho, corpo) {
  ocupado = true;
  erroEl.hidden = true;
  try {
    const resposta = await fetch(caminho, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo || {}),
    });
    const data = await resposta.json();
    if (!resposta.ok && data.mensagem) {
      data.erro = data.mensagem;
    }
    if (caminho === "/api/iniciar") {
      cursor = 0;
      logEl.replaceChildren(vazio);
      vazio.hidden = false;
    }
    aplicar(data);
  } catch (erro) {
    erroEl.hidden = false;
    erroEl.textContent = "A interface não respondeu.";
  } finally {
    ocupado = false;
    try {
      await atualizar();
    } catch (_erro) {
      /* o próximo ciclo tenta de novo */
    }
  }
}

botoes.iniciar.addEventListener("click", () => {
  acao("/api/iniciar", { modelo: modelo.value, idioma: idioma.value.trim() || "pt" });
});
botoes.pausar.addEventListener("click", () => acao("/api/pausar"));
botoes.continuar.addEventListener("click", () => acao("/api/continuar"));
botoes.encerrar.addEventListener("click", () => acao("/api/encerrar"));

async function ciclo() {
  try {
    await atualizar();
  } catch (_erro) {
    estadoTexto.textContent = "Sem resposta";
  }
}

ciclo();
setInterval(ciclo, 1000);
