"""
O estado que devia acumular não pode morrer no deploy.

Descoberto em 25/08/2026, e a forma como apareceu é o motivo destes testes
existirem: só o subdiretório `/var/cache/premercado/hist` tinha volume
nomeado. Os overlays (earnings, correlações, capex, traduções) e o contador de
orçamento diário viviam na camada gravável do container, e cada
`docker compose up --build` apagava todos.

O sintoma foi ficando caro em três degraus:

1. Trabalho repetido -- o ritual de "rodar atualizar_earnings depois de todo
   deploy" era o conserto manual disto, e ninguém tinha perguntado por quê.
2. Dado errado -- o coletor de capex mescla com o histórico guardado para não
   gastar cota rebuscando o que já tem. Com o overlay zerado ele recomeça do
   zero, e quando a cota acaba no meio, empresas ficam rasas: META e ORCL
   voltaram com 5 trimestres em vez de 40.
3. Orçamento que não protege -- o contador da Alpha Vantage ficava em /tmp,
   que também morre no deploy. A nossa conta zerava a cada `up --build`
   enquanto a AV seguia contando o dia dela; o resultado foi a própria AV
   dizendo "sua chave já bateu o limite de 25 requisições por dia" enquanto o
   nosso orçamento de 15 achava que estava intacto.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_cache_persistente.py -v
"""
import pathlib
import re

_RAIZ = pathlib.Path(__file__).resolve().parents[4]
_COMPOSE_TXT = (_RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
_CACHE = "/var/cache/premercado"

# Sem PyYAML de propósito: ele não está no requirements.txt, e puxar uma
# dependência de produção para ler o compose num teste seria pagar caro por
# pouco. As linhas de montagem têm formato fixo ("- nome:/caminho"), e o
# arquivo é nosso.


def _montagens_do_app() -> list:
    """[(volume, destino)] das montagens declaradas no serviço app."""
    return [(m.group(1), m.group(2)) for m in
            re.finditer(r"^\s+- ([A-Za-z0-9_]+):(/\S+)$", _COMPOSE_TXT, re.M)]


def _volumes_declarados() -> set:
    """Os nomes sob o `volumes:` de topo (coluna zero, no fim do arquivo)."""
    bloco = _COMPOSE_TXT[_COMPOSE_TXT.rindex("\nvolumes:"):]
    return set(re.findall(r"^  ([A-Za-z0-9_]+):\s*$", bloco, re.M))


def test_o_diretorio_de_cache_inteiro_esta_em_volume_nomeado():
    destinos = {destino for _, destino in _montagens_do_app()}
    assert _CACHE in destinos, (
        "sem volume no diretório inteiro, todo `up --build` apaga os overlays")


def test_o_volume_do_cache_esta_declarado():
    nomes = {vol for vol, destino in _montagens_do_app() if destino == _CACHE}
    declarados = _volumes_declarados()
    assert nomes and nomes <= declarados, f"volume não declarado: {nomes - declarados}"


def test_o_cache_de_historico_continua_com_volume_proprio():
    """Mount mais específico ganha do genérico -- os dois têm que coexistir,
    e perder o de hist/ significaria rebaixar um cache grande e caro."""
    destinos = {destino for _, destino in _montagens_do_app()}
    assert f"{_CACHE}/hist" in destinos


def test_todo_overlay_do_agente_mora_sob_o_diretorio_persistido():
    """Varre os caminhos declarados no código: um overlay novo em outro lugar
    volta a morrer no deploy, e o teste tem que pegar isso antes da produção."""
    agente = _RAIZ / "artifacts/api-server/src/agent"
    fora = []
    for py in agente.glob("*.py"):
        for caminho in re.findall(r'"(/var/[^"]*\.json)"', py.read_text(encoding="utf-8")):
            if not caminho.startswith(_CACHE + "/"):
                fora.append(f"{py.name}: {caminho}")
    assert fora == [], f"overlay fora do diretório persistido: {fora}"


def test_o_contador_de_orcamento_nao_mora_em_tmp():
    from agent import provider_health
    assert provider_health._PATH.startswith(_CACHE + "/"), (
        "orçamento que o deploy zera não é orçamento: a AV continua contando "
        "o dia dela enquanto a nossa conta recomeça")
