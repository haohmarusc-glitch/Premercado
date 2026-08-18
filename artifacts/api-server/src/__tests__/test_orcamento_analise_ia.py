"""
Invariante de orçamento de tempo da Análise Rápida com IA.

Playbook §3: nenhuma camada interna pode ter orçamento MAIOR que o timeout
externo. Se tiver, o Node só descobre o problema matando o subprocesso, e o
usuário recebe um 500 genérico em vez de um erro legível -- foi exatamente o
que aconteceu em 17/08/2026.

Reconstrução do incidente:

  routes/analysis.ts .................... 90s de teto
  API_TIMEOUT_SECONDS (default) ......... 60s por chamada
  AGENT_MAX_RETRIES (SDK, default 1) .... 2 tentativas
  AGENT_TRANSIENT_RETRIES (default 1) ... 2 tentativas
  -> pior caso por PROVEDOR: 2 × (2 × 60s) + backoff ≈ 245s

Uma análise passou em 57,5s -- já encostando no timeout de 60s da própria
API, o que era o sintoma. As duas seguintes bateram 90s cravados:
"Failed: /analise-rapida/ia", 500 na tela, e o log dizia só "timeout" porque
o stderr (com as linhas [provider]) era descartado.

Este teste lê os DOIS lados -- o TypeScript e o Python -- e falha se alguém
mexer num sem mexer no outro. É o tipo de acoplamento que nenhum typecheck
pega, porque atravessa linguagens.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_orcamento_analise_ia.py -v
"""
import pathlib
import re

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent
_ROTA = (_SRC / "routes" / "analysis.ts").read_text(encoding="utf-8")


def _timeout_da_rota_s() -> float:
    """O setTimeout da runAnaliseRapidaIA, em segundos."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    m = re.search(r"setTimeout\((?:.|\n)*?\}?,\s*([\d_]+)\s*\)", trecho)
    assert m, "não achei o setTimeout de runAnaliseRapidaIA"
    return int(m.group(1).replace("_", "")) / 1000


def _modulo():
    from agent import analise_rapida_ia as mod
    return mod


def test_orcamento_do_python_cabe_no_timeout_do_node():
    """A invariante. Se quebrar, o processo volta a ser morto no meio em vez
    de devolver erro legível."""
    mod = _modulo()
    assert mod._ORCAMENTO_TOTAL_S < _timeout_da_rota_s(), (
        f"orçamento interno ({mod._ORCAMENTO_TOTAL_S}s) >= timeout do Node "
        f"({_timeout_da_rota_s()}s) — ver playbook §3"
    )


def test_duas_tentativas_de_provedor_cabem_no_orcamento():
    """A cadeia de fallback precisa conseguir trocar de provedor ao menos uma
    vez. Com uma tentativa só, um provedor lento derruba a análise inteira em
    vez de cair para o próximo.

    Este teste JÁ EXISTIA e passava -- usando 20s de coleta fundamental como
    suposição que nada no código garantia. Em 18/08/2026 a suposição falhou em
    produção: a coleta mais a primeira chamada consumiram 124s dos 135s, e a
    troca de provedor ficou inalcançável exatamente no caso para o qual foi
    escrita. Aritmética certa no papel, livre na prática.

    Agora o teto da coleta é constante do módulo (_TETO_FUNDAMENTO_S) e a
    conta amarra código, não estimativa.

    ATUALIZAÇÃO 18/08/2026 -- e a conta AINDA estava errada, de um jeito mais
    fundo. Ela pressupõe que uma chamada a `create()` é UMA tentativa de
    provedor. Não é: a cadeia de fallback percorre os provedores por DENTRO da
    chamada, sem devolver o controle. Com seis configurados, um `create()` pode
    custar 6 x 55s.

    Este teste continua valendo como piso -- o orçamento precisa comportar ao
    menos duas tentativas -- mas quem GARANTE o teto agora é o prazo passado ao
    cliente (definir_orcamento), testado em test_orcamento_da_cadeia.py.
    Aritmética de fora não segura laço de dentro."""
    mod = _modulo()
    assert 2 * mod._LLM_TIMEOUT_S + mod._TETO_FUNDAMENTO_S <= mod._ORCAMENTO_TOTAL_S


def test_a_camada_opcional_tem_teto_proprio():
    """A camada fundamental é fail-open por projeto, mas "opcional" sem teto
    de TEMPO não é opcional: o que ela consome sai do LLM, que é obrigatório.
    yfinance.info sozinho já levou dezenas de segundos em produção."""
    mod = _modulo()
    assert mod._TETO_FUNDAMENTO_S > 0
    assert mod._TETO_FUNDAMENTO_S < mod._ORCAMENTO_TOTAL_S


def test_a_rota_registra_o_stderr_quando_a_analise_tropeca_e_sai(monkeypatch):
    """O caso CARO é o intermediário: um provedor trunca (e a tentativa perdida
    É cobrada -- tokens de raciocínio contam como saída), o seguinte entrega o
    texto, e o desfecho é sucesso. Registrar só no erro deixava exatamente esse
    caminho sem rastro.

    Produção 18/08/2026: análise a US$ 0,0608 contra os ~US$ 0,015 esperados, e
    `docker compose logs | grep analise_rapida_ia` vazio -- não por falha do
    log, mas porque a execução tinha dado certo."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    assert "MARCAS_DE_TROPECO" in trecho
    # As três marcas que o Python imprime quando a cadeia anda.
    for marca in ("pulando", "truncou", "toco"):
        assert marca in trecho, f"'{marca}' não é reconhecida como tropeço"


def test_a_regra_de_unidade_de_tempo_esta_no_system():
    """Número certo com unidade errada é pior que número errado: o valor
    confere com o JSON, então o leitor não tem como desconfiar. Visto em
    produção -- `momentumAnnualPct` (taxa anualizada) descrito como
    'momentum de 106,56% em 90 dias', que seria ~38%."""
    mod = _modulo()
    assert "momentumAnnualPct" in mod.SYSTEM
    assert "ANUALIZADA" in mod.SYSTEM


def test_a_regra_de_escala_da_volatilidade_esta_no_system():
    """Resíduo da mesma família do momentum, achado no dia seguinte (SNDK):
    `volAnnual` chega como FRAÇÃO (1,169) e saiu no texto como "a volatilidade
    anual é 1,17", ao lado de "beta 1,99". Um é percentual, o outro é
    adimensional, e escritos crus parecem a mesma grandeza.

    A regra precisa dizer as DUAS metades -- o que converter e o que NÃO --
    senão o modelo passa a carimbar % em beta e RVOL."""
    mod = _modulo()
    assert "FRAÇÃO" in mod.SYSTEM
    assert "volAnnual" in mod.SYSTEM
    # a metade que impede o excesso de zelo
    assert "adimensionais" in mod.SYSTEM


def test_o_dockerfile_tem_store_persistente_do_pnpm():
    """COPY . . antes do install é decisão consciente (ver comentário lá): a
    alternativa exigiria listar os package.json à mão e sair de sincronia em
    silêncio. O custo é o install rodar sempre -- e sem store persistente ele
    rebaixava 934 pacotes por deploy ("reused 0", medido em 18/08/2026).

    O cache mount resolve sem tocar naquela decisão: não é camada, sobrevive à
    invalidação do COPY, e o install continua rodando com --frozen-lockfile.
    Este teste existe para o mount não ser removido junto com algum refactor
    do Dockerfile -- a perda seria invisível, só um deploy mais lento."""
    dockerfile = (_SRC.parent.parent.parent / "Dockerfile").read_text(encoding="utf-8")
    assert "--mount=type=cache" in dockerfile
    assert "PNPM_STORE_DIR" in dockerfile
    # --frozen-lockfile continua mandando: cache de velocidade, não de corretude
    assert "--frozen-lockfile" in dockerfile


def test_o_resultado_pronto_e_guardado_para_a_reconexao():
    """A análise leva ~58s (medido: um 200 com responseTime 58608) e nesse
    tempo a conexão do celular cai. Quando cai, o servidor NÃO cancela o
    Python: o trabalho termina, os tokens são cobrados, e a resposta é escrita
    num socket que não existe mais.

    A resposta certa não é evitar a queda -- rede móvel vai cair -- é fazer com
    que ela não custe nada."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    assert "cacheIA" in trecho
    assert "guardarIA" in trecho


def test_erro_nao_e_guardado_no_cache():
    """Guardar um erro transformaria falha passageira (provedor fora do ar,
    orçamento estourado) em dez minutos de falha GARANTIDA -- o usuário
    clicaria de novo e receberia o mesmo erro instantaneamente, sem nem tentar."""
    corpo = _ROTA.split("function guardarIA", 1)[1].split("\n}", 1)[0]
    assert '"error" in valor' in corpo
    assert "return" in corpo


def test_a_carona_no_coalescer_tem_idade_maxima():
    """Embarcar num trabalho que já gastou o orçamento não é economia, é herdar
    uma morte marcada: em produção uma requisição de 68s morreu por um timeout
    de 150s, quando 58s bastavam."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    assert "IDADE_MAX_CARONA_MS" in _ROTA
    assert "IDADE_MAX_CARONA_MS" in trecho, "a idade máxima precisa ser PASSADA ao coalescer"


def test_a_idade_maxima_da_carona_cabe_no_timeout_da_rota():
    """A invariante: quem entra de carona precisa de tempo para ao menos uma
    passada completa. Idade máxima + uma análise inteira tem que caber no teto
    da rota, senão o parâmetro não protege de nada."""
    m = re.search(r"IDADE_MAX_CARONA_MS\s*=\s*([\d_]+)", _ROTA)
    assert m, "não achei IDADE_MAX_CARONA_MS"
    idade_max_s = int(m.group(1).replace("_", "")) / 1000
    # A análise medida em produção levou 58,6s.
    assert idade_max_s + 58.6 <= _timeout_da_rota_s()


def test_nenhum_diagnostico_do_provider_vai_para_stdout():
    """A regra do projeto: stdout é do resultado, stderr é do diagnóstico.

    Quebrada em provider.py até 18/08/2026 -- as quatro linhas `[provider]`
    iam para stdout, que em analise_rapida_ia.py é CONTRATUALMENTE do JSON
    final. O efeito era duplo: o diagnóstico sumia (todo `grep [provider]`
    daquele dia voltou vazio, porque as linhas estavam dentro do JSON) e o
    pipe era poluído -- o "parse resiliente" da rota existe para sobreviver a
    exatamente isso, tratando o sintoma.

    Este teste vale para o arquivo INTEIRO, não só para as linhas de hoje:
    qualquer print novo em stdout aqui volta a esconder o diagnóstico."""
    provider_py = (_SRC / "agent" / "provider.py").read_text(encoding="utf-8")

    # print( multilinha: o file=sys.stderr costuma vir numa linha seguinte, e
    # olhar linha a linha daria falso positivo. Cada bloco vai até o ")" que o
    # fecha.
    linhas = provider_py.splitlines()
    em_stdout: list[str] = []
    i = 0
    while i < len(linhas):
        if "print(" in linhas[i] and not linhas[i].strip().startswith("#"):
            bloco = []
            j = i
            while j < len(linhas) and j < i + 10:
                bloco.append(linhas[j])
                if linhas[j].rstrip().endswith(")"):
                    break
                j += 1
            texto = "\n".join(bloco)
            # PROVIDER_DOWN é a ÚNICA exceção, e não é diagnóstico humano: é
            # canal legível por máquina, lido por runner.ts e
            # report-preflight.ts no stdout CRU para montar o banner de
            # "provedor caído". Movê-lo quebraria o banner em silêncio.
            if "sys.stderr" not in texto and "PROVIDER_DOWN" not in texto:
                em_stdout.append(linhas[i].strip() + " ...")
            i = j + 1
            continue
        i += 1

    assert em_stdout == [], f"print sem stderr em provider.py: {em_stdout}"


def test_teto_de_tokens_cresce_para_modelo_que_pensa():
    """Em modelo thinking o max_tokens cobre raciocínio + resposta, não só a
    resposta -- o mesmo número significa coisas diferentes por provedor.

    Medido: deepseek-v4-pro gastou 17.147 chars de raciocínio (~4.300 tokens) e
    devolveu resposta VAZIA com stop_reason=length."""
    mod = _modulo()
    assert mod.teto_de_tokens("claude-sonnet-5") == mod.MAX_TOKENS
    assert mod.teto_de_tokens("deepseek-v4-flash") == mod.MAX_TOKENS
    # o que pensa ganha a folga
    assert mod.teto_de_tokens("deepseek-v4-pro") == mod.MAX_TOKENS + mod.MAX_TOKENS_RACIOCINIO
    assert mod.teto_de_tokens("some-reasoner-v2") > mod.MAX_TOKENS


def test_a_folga_cobre_o_raciocinio_medido():
    """4.300 tokens medidos + a resposta pedida (400-700 palavras) têm que
    caber. Folga menor que o caso real já visto não protege de nada."""
    mod = _modulo()
    tokens_medidos = 4300
    assert mod.MAX_TOKENS_RACIOCINIO >= tokens_medidos


def test_o_teto_por_modelo_e_de_fato_usado_na_chamada():
    """Declarar a função e continuar mandando MAX_TOKENS cru passaria nos
    testes acima sem corrigir nada."""
    mod = _modulo()
    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "max_tokens=teto_de_tokens(" in fonte


def test_o_tempo_e_registrado_dividido_entre_coleta_e_llm():
    """Em 18/08/2026 o erro dizia "143s de 135s" e nada sobre onde os 143s
    foram parar -- descobrir se a culpa era das fontes ou do LLM exigiu
    aritmética sobre um número só."""
    mod = _modulo()
    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "coleta terminou em" in fonte
    assert "respondeu em" in fonte


def test_a_rota_registra_o_stderr_quando_o_script_devolve_erro():
    """O script sai com código 0 E {"error": ...} quando nenhum provedor
    produz texto -- é o que a Tarefa 0 passou a fazer. Sem registrar o stderr
    NESSE caminho, todo o diagnóstico morre na variável `err`: em 18/08/2026 a
    tela mostrou "0 chars" e o log do container não tinha uma linha sobre a
    causa.

    Trocar um 500 mudo por um erro elegante não pode significar trocar um erro
    legível por um erro bonito e inauditável."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    assert "registrarDiagnostico" in trecho, "o caminho de sucesso-com-erro não registra o stderr"
    # E precisa valer nos DOIS parses (bloco inteiro e última linha), senão
    # stdout poluído volta a engolir o diagnóstico.
    assert trecho.count("registrarDiagnostico(parsed)") == 2


def test_retries_desligados_para_esta_rota():
    """Retry do SDK e do fallback multiplicam o tempo sem coordenação (2×2=4
    tentativas de 60s = 240s). Numa rota interativa o certo é trocar de
    provedor, não insistir no mesmo."""
    import os

    _modulo()  # o import é que aplica as variáveis
    assert os.environ["AGENT_MAX_RETRIES"] == "0"
    assert os.environ["AGENT_TRANSIENT_RETRIES"] == "0"
    assert float(os.environ["API_TIMEOUT_SECONDS"]) == pytest.approx(_modulo()._LLM_TIMEOUT_S)


def test_a_rota_registra_o_stderr_no_timeout():
    """Sem isso o log dizia só "timeout" e não dava para saber qual provedor
    consumiu o tempo — foi o que impediu o diagnóstico na primeira ocorrência."""
    trecho = _ROTA.split("function runAnaliseRapidaIA", 1)[1]
    assert "logger.error" in trecho
    assert "stderr" in trecho
