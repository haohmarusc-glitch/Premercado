"""
Validador da Análise Rápida -- confere a SAÍDA, não o prompt.

Por que este arquivo existe. O `SYSTEM` de analise_rapida_ia.py declara 18
regras, e `test_analise_rapida_ia.py` verifica que elas continuam ESCRITAS lá
("a string ainda contém 'nunca R$'"). Isso protege contra alguém apagar a
regra ao consolidar o prompt -- e não protege contra o modelo desobedecê-la.
Era a mesma forma exata que a leitura da cesta tinha até 25/08/2026, quando um
texto chamou de "padrão estatisticamente relevante" uma correlação com p
corrigido de 0,462. Regra no prompt sem conferência é sugestão.

O que este módulo NÃO faz: reescrever ou esconder o texto. Ele DECLARA o que
não fecha; quem chama decide (hoje: uma retentativa com os apontamentos e, se
persistir, publicação COM os avisos ao lado).

O CRITÉRIO das checagens é RISCO DE ALARME FALSO, não importância. Duas regras
do prompt ficaram deliberadamente de fora -- "volatilidade chega como fração" e
"todo número vem do JSON" -- porque só dariam para checar por heurística, e
validador que grita à toa ensina o leitor a ignorar o bloco amarelo. Aí ele
fica pior que nenhum.

REESCRITA DE 26/08/2026. Três rodadas reais seguidas produziram três falsos
positivos (WOLF, ADI, ADI), e uma auditoria dos dois validadores reproduziu 39
defeitos. Praticamente todos eram a MESMA falha em roupas diferentes: casar
TOKEN em vez de AFIRMAÇÃO. As primitivas comuns (fronteira numérica,
antinegação, leitura defensiva de payload) moraram em `validador_nucleo`, e
cada checagem daqui passou a exigir o predicado, não a co-ocorrência.
"""
import re

from .validador_nucleo import (afirmacao_negada, afirmado_sem_negacao,
                               booleano, caminho, cita_numero, dic,
                               frase_com_moeda_errada, frases, grafias,
                               minusculas, num_finito, sem_acento,
                               sem_blocos_de_codigo, texto_utilizavel)
from .validador_nucleo import (avisos, bloco_de_correcao as _bloco,  # noqa: F401
                               erros, linha_de_log, resumo_legivel)

# Reexportado com o nome antigo: a fronteira numérica nasceu aqui (falso
# positivo do ADI) e migrou para o núcleo quando os três validadores passaram
# a compartilhá-la. Quem já importava daqui continua funcionando.
_cita_numero = cita_numero

# As seções que o prompt pede "EXATAMENTE". A Síntese é a que mais importa
# checar: o próprio SYSTEM avisa que ela "é a primeira a se perder quando o
# texto estica", o que é uma previsão de falha esperando conferência.
SECOES_OBRIGATORIAS = (
    "Quadro geral", "Fundamento e valuation", "Leitura técnica",
    "Níveis que importam", "Earnings e volatilidade", "Síntese",
)

# Recomendação explícita. O prompt: "NÃO recomende comprar ou vender. Descreva
# cenários e níveis de invalidação; a decisão é do leitor."
#
# A lista antiga cobria seis construções e deixava passar as mais comuns em
# pt-BR: o imperativo ("Compre WOLF"), a forma nominal com preposição
# ("recomendação DE compra"), o condicional ("eu compraria") e o rótulo de
# casa de análise ("BUY"). Numa regra que o prompt trata como dura, cobrir
# pouco é pior que não checar -- dá sensação de anteparo sem o anteparo.
_RECOMENDA = (
    r"recomend\w*\s+(?:a\s+|de\s+|da\s+)?(?:compra|venda|comprar|vender)",
    r"(?:minha|nossa)\s+(?:recomenda[cç][ãa]o|indica[cç][ãa]o|sugest[ãa]o)",
    r"sugiro\s+(?:que\s+)?(?:comprar|vender|compre|venda)",
    r"vale\s+(?:a\s+pena\s+)?(?:comprar|vender)",
    r"hora\s+de\s+(?:comprar|vender)",
    r"momento\s+de\s+(?:comprar|vender)",
    r"deve[-\s]?se\s+(?:comprar|vender)",
    r"(?:o\s+)?investidor\s+deveria\s+(?:comprar|vender|montar|entrar|sair)",
    r"aconselho\s+(?:a\s+)?(?:comprar|vender)",
    # Imperativo: "Compre WOLF agora", "Venda antes do balanço".
    #
    # O imperativo tem que ABRIR a oração. Em pt-BR "venda" é também
    # substantivo, e o sistema imprime "Sinal: VENDA" no próprio painel -- o
    # texto que reporta isso ("um sinal de venda", WOLF 26/08/2026) estava
    # obedecendo, não recomendando. `frases()` já entrega uma frase por vez,
    # então `^` é o início dela.
    r"(?:^|[.;:!?]\s*|,\s+)(?:compre|venda|vendam|comprem)\b"
    r"(?!\s+(?:de|em|no|na)\b)",
    # Condicional de primeira pessoa: "eu compraria", "eu abriria posição".
    r"\beu\s+(?:compraria|venderia|abriria|montaria|entraria|sairia)\b",
    r"\b(?:compraria|venderia)\b",
    r"melhor\s+(?:comprar|vender|entrar|sair)\b",
    r"(?:boa|[óo]tima)\s+(?:oportunidade|hora)\s+de\s+(?:compra|entrada)",
    r"\b(?:buy|sell|strong\s+buy|strong\s+sell)\b",
    r"montar\s+posi[cç][ãa]o\s+(?:agora|j[áa])",
)
# Meta-frase: o modelo dizendo que NÃO vai recomendar é obediência, não
# violação. Sem esta exceção o validador puniria justamente quem acertou.
# A checagem usa a FRASE inteira como janela: a versão anterior olhava 40
# caracteres antes do match e perdia a negação em frases longas ("Dada a
# análise atual e todos os indicadores, não é hora de comprar").
_META_RECUSA = (r"n[ãa]o\s+(?:vou\s+|cabe\s+|[ée]\s+papel\s+)?recomend",
                r"sem\s+recomenda", r"n[ãa]o\s+[ée]\s+recomenda[cç][ãa]o",
                r"a\s+decis[ãa]o\s+[ée]\s+d[oe]\s+leitor")

# R1/R2/S1/S2 são bandas estatísticas (preço ± reação média a earnings), não
# suporte/resistência do gráfico. Estes padrões rodam sobre o texto em
# minúsculas COM acento, porque sem acento "é" e "e" viram a mesma letra -- e a
# diferença entre "R1 É a resistência" (identificação errada) e "R1 E a
# resistência" (lista de duas coisas) é justamente essa.
_BANDA = r"\b[rs][12]\b"
_NIVEL = (r"\b(?:suportes?|resist[êe]ncias?|pisos?|zonas?\s+de\s+defesa"
          r"|tetos?\s+t[ée]cnicos?|fundos?\s+t[ée]cnicos?)\b")
_LIGA = (r"(?:é|são|era|eram|vira|viram|virou|funciona\w*\s+como"
         r"|atua\w*\s+como|configura\w*[-\s]se\s+como|constitu\w+|form\w+"
         r"|aparec\w+\s+como|serve\w*\s+(?:de|como)|representa\w*"
         r"|equivale\w*\s+a|marca\w*)")
# O vão passou de 40 para 90: em produção o modelo escreve o valor e a fonte
# entre a banda e o predicado ("o S1 (banda de reação) em US$ 357,14 e a MM200
# (US$ 343,15) configuram-se como suportes"), e 40 caracteres não alcançavam.
# `[^.;]` continua barrando a travessia de frase.
_BANDA_VIRA_NIVEL = (rf"{_BANDA}[^.;]{{0,90}}?\s{_LIGA}\s+"
                     rf"(?:o|a|os|as|um|uma|de|do|da)?\s*{_NIVEL}")
_NIVEL_VIRA_BANDA = rf"{_NIVEL}\s+(?:em|de|do|da|no|na)?\s*\(?{_BANDA}"
# A banda ARROLADA entre os níveis: "as resistências imediatas INCLUEM a MM50
# e o R1". O verbo é restrito a "incluir/compreender" de propósito -- com um
# `ser` genérico aqui, "a resistência é a máxima de julho e R1 fica acima"
# (frase que DISTINGUE os dois) viraria apontamento.
_NIVEL_ARROLA_BANDA = (rf"{_NIVEL}[^.;]{{0,30}}?\s(?:inclu\w+|compreend\w+|"
                       rf"abrang\w+|contempl\w+)\s[^.;]{{0,60}}?{_BANDA}")
# Cópula REVERSA: "o suporte imediato É A S1". Só o sentido banda→nível estava
# coberto, e a rodada de INTC (26/08/2026) publicou este sem apontamento.
# O vão é curto e não atravessa vírgula: "a resistência do gráfico é a máxima
# de julho e R1 fica acima" distingue os dois e tem que passar.
_NIVEL_E_A_BANDA = (rf"{_NIVEL}[^.;,]{{0,25}}?\s(?:é|são|seria[m]?|fica[m]?|"
                    rf"est[áa]|est[ãa]o)\s+(?:o|a|os|as|em|n[oa]s?)?\s*\(?{_BANDA}")
# Cópula elíptica: "R2 é o teto E S2 O PISO" -- o verbo aparece uma vez só e
# a segunda banda fica colada ao artigo. Aqui NÃO cabe vão nenhum: com o
# `[^.;]{0,40}?` do padrão acima, um artigo solto casaria com qualquer
# "resistência" que aparecesse depois na frase.
_BANDA_ELIPSE = rf"{_BANDA}\s+(?:o|a|os|as)\s+{_NIVEL}"

# O texto que NOMEIA a natureza da banda na mesma frase já disse ao leitor o
# que ela é. "o S1 (banda de reação) ... configura-se como suporte" usa a
# palavra proibida, mas não engana sobre a origem do número -- e acusá-lo
# seria o quarto alarme falso desta checagem em quatro rodadas reais.
_ROTULA_A_BANDA = (r"banda\s+(?:de\s+rea[cç][ãa]o|estat[íi]stica|"
                   r"de\s+volatilidade)|rea[cç][ãa]o\s+m[ée]dia|"
                   r"desvio\s+t[íi]pico")

# O nível QUALIFICADO como estatístico. O SYSTEM proíbe chamar as bandas de
# "suporte e resistência DO GRÁFICO"; "o primeiro suporte ESTATÍSTICO é S1"
# (WOLF, 26/08/2026) faz exatamente a distinção pedida, e apontá-la seria
# punir quem acertou. Tem que estar COLADO ao nível: um "estatístico" solto
# em qualquer lugar da frase não qualifica nada.
_NIVEL_QUALIFICADO = (rf"{_NIVEL}\s+(?:estat[íi]stic\w+|de\s+rea[cç][ãa]o|"
                      rf"de\s+volatilidade)|estat[íi]stic\w+\s+{_NIVEL}")

# Moeda: os ativos são listados nos EUA e o prompt manda não converter. Mas o
# modelo ECOANDO a regra ("não converter para R$") ou citando o câmbio não
# está desobedecendo -- é a mesma armadilha de token-em-vez-de-afirmação.

# Níveis de referência que o texto compara com o PREÇO, e como o modelo os
# escreve. O valor vem do mesmo campo que alimenta a lista de níveis do prompt.
_REFERENCIAS = (
    ("MM20",  r"mm\s?20|m[ée]dia\s+(?:m[óo]vel\s+)?de\s+20",
     (("technicals", "sma20"),)),
    ("MM50",  r"mm\s?50|m[ée]dia\s+(?:m[óo]vel\s+)?de\s+50",
     (("snapshot", "sma50"), ("technicals", "sma50"))),
    ("MM200", r"mm\s?200|m[ée]dia\s+(?:m[óo]vel\s+)?de\s+200",
     (("snapshot", "sma200"), ("technicals", "sma200"))),
    ("VWAP",  r"vwap", (("technicals", "vwap"),)),
)

# "está 0,72% ACIMA da MM20" -- a direção afirmada, com o número ao lado.
_DIRECAO = r"(\d+(?:[.,]\d+)?)\s*%\s*(acima|abaixo)\s+d[ao]s?\s+(?:sua\s+)?"

# Estatísticas de earnings que o texto costuma nomear, e os campos do resumo
# que as bancam. O nome do campo entra na mensagem porque a forma típica do
# erro não é inventar número: é pegar o número CERTO de outra linha do resumo
# e dar a ele o rótulo errado.
_ESTATISTICAS = (
    ("gap de abertura", r"gap|abertura|descolamento",
     ("gap_pct_mean", "gap_pct_abs_mean")),
    ("fechamento do dia", r"fechamento|fech\.",
     ("close_pct_mean", "close_pct_abs_mean")),
)
# Todos os campos do resumo que são percentuais médios — para dizer QUAL deles
# o número citado realmente é, quando não for o rotulado.
_CAMPOS_DE_RESUMO = ("gap_pct_mean", "gap_pct_abs_mean", "close_pct_mean",
                     "close_pct_abs_mean", "close_pct_std",
                     "intraday_range_pct_mean")

# Marcas que ORDENAM dois níveis, uma depois da outra. "e" não entra: ligar
# dois níveis numa lista não afirma qual vem primeiro.
_SEQUENCIA = (r"seguid[ao]s?\b", r"em\s+seguida", r"na\s+sequ[êe]ncia",
              r"logo\s+(?:acima|abaixo|depois|em\s+seguida)", r"mais\s+adiante",
              r"a\s+seguir", r"depois\s+(?:vem|dela|dele|disso)", r"e\s+ent[ãa]o")

# Distância assinada de um nível ao preço, como o modelo escreve: "(+10,33%)".
# O sinal EXPLÍCITO é exigido de propósito -- sem ele "subiu 2%, depois caiu
# 5%" pareceria uma sequência crescente quando são lados opostos.
_DISTANCIA_ASSINADA = r"([+-]\d+(?:[.,]\d+)?)\s*%"

# O nível escrito em DÓLAR, sem percentual ao lado -- foi assim que a rodada
# de WOLF (26/08/2026) publicou a ordem trocada sem apontamento: "US$ 28,48,
# onde está a MM200, atua como resistência IMEDIATA, seguido pela MM20 em
# US$ 28,16". A MM20 está mais perto. Aqui a distância vem do preço.
# `(?i)` porque esta checagem roda sobre `prosa_sa`, que é MINÚSCULA -- a
# mesma armadilha que já tinha matado a checagem de moeda com "R$".
_VALOR_EM_DOLAR = r"(?i)us\$\s*(\d+(?:[.,]\d+)?)"

# A frase tem que estar falando de NÍVEL. Sem isto, "o alvo de US$ 40 foi
# cortado, seguido pelo de US$ 35" viraria apontamento sobre alvo de analista,
# que não se ordena por distância.
_FALA_DE_NIVEL = (r"n[íi]vel|n[íi]veis|suporte|resist[êe]nc|m[ée]dia\s+m[óo]vel|"
                  r"\bmm\s?\d|banda|\b[rs][12]\b|m[áa]xima|m[íi]nima|vwap|"
                  r"patamar")

# Onde o papel está DENTRO da faixa de 52 semanas, dito por extenso. O modelo
# tende a derivar isso das duas distâncias que ele mesmo escreveu, e derivar é
# justamente onde ele erra.
_METADE_DE_CIMA = (r"metade\s+superior", r"parte\s+(?:de\s+cima|superior)",
                   r"topo\s+d[ao]\s+(?:faixa|intervalo|range)",
                   r"pr[óo]xim\w+\s+d[ao]\s+m[áa]xima",
                   r"perto\s+d[ao]\s+m[áa]xima")
_METADE_DE_BAIXO = (r"metade\s+inferior", r"parte\s+(?:de\s+baixo|inferior)",
                    r"fundo\s+d[ao]\s+(?:faixa|intervalo|range)",
                    r"pr[óo]xim\w+\s+d[ao]\s+m[íi]nima",
                    r"perto\s+d[ao]\s+m[íi]nima")
# A frase tem que estar falando da FAIXA, não de outra coisa qualquer.
# Os blocos da camada fundamental, com o nome que o texto usa para cada um.
# As chaves espelham `analise_rapida_ia.COLETORES` -- `test_blocos_espelham_os_coletores`
# confere que as duas listas não divergem.
_BLOCOS_FUNDAMENTAIS = (
    ("alvosAnalistas", "alvos de analistas"),
    ("valuation", "valuation/DCF"),
    ("manchetes", "manchetes"),
)

# "nao estavam disponiveis", "nao foi possivel obter", "nao ha dados de".
# Precisa da NEGACAO junto: "os dados disponiveis mostram" e' afirmacao.
_NEGA_DISPONIBILIDADE = (
    r"n[aã]o\s+(?:est\w+|foi|foram|h[aá]|existe\w*|disp\w+|constam?)"
    # `obt\w+` e nao `obtid\w+`: "nao foi possivel OBTER" e' a forma mais
    # comum, e o particpio sozinho deixava o infinitivo de fora.
    # Os participios acompanham os verbos de entrega do bloco abaixo: a forma
    # "nao FORAM FORNECIDOS" e' auxiliar + participio e nao casa no ramo de
    # verbo direto ("nao forneceu"). Faltando aqui, "nao foram fornecidos"
    # escapava -- foi assim que a 2a rodada do MRVL passou.
    r"[^.]{0,40}?(?:dispon[íi]ve\w+|acess[íi]ve\w+|obt\w+|encontrad\w+"
    r"|forneci\w+|apresentad\w+|disponibilizad\w+|inclu[íi]d\w+|trazid\w+)"
    r"|indispon[íi]ve\w+"
    # MRVL, 27/08/2026: "o JSON nao TROUXE camada fundamental" e "os alvos
    # ... nao foram FORNECIDOS" escaparam nas DUAS rodadas do mesmo ticker,
    # com `alvosAnalistas` no payload. O verbo de entrega faltava no
    # vocabulario: "veio/chegou/retornou" estavam, "trouxe/forneceu/
    # apresentou/disponibilizou" nao -- e negar dado presente com um verbo
    # sinonimo e' o mesmo defeito que a checagem existe para pegar.
    r"|n[aã]o\s+(?:vie\w+|veio|chegou|chegaram|retorn\w+|trouxe|trouxeram|"
    r"forne\w+|apresent\w+|disponibiliz\w+|inclu[ií]\w*|contempl\w+)"
    r"|sem\s+(?:dados|informa[cç][õo]es)\s+(?:de\s+)?(?:fundament\w+|valuation)")

# Qual BLOCO a frase esta' negando. Sem isto a checagem so' pergunta "a frase
# fala de fundamento?", e uma frase CORRETA sobre o bloco ausente ("a FMP nao
# forneceu o DCF") viraria ERRO so' porque OUTRO bloco veio. O vocabulario de
# verbos acima aumenta o alcance da checagem, entao a precisao tem que subir
# junto -- ampliar o gatilho sem estreitar o alvo troca um falso negativo por
# um falso positivo.
_TERMOS_DO_BLOCO = {
    "alvosAnalistas": r"alvos?\s+d\w+\s+analist\w+|pre[cç]o[- ]alvo|consenso",
    "valuation": r"valuation|\bdcf\b|fluxo\s+de\s+caixa|m[uú]ltiplos?|p/l\b|p/vp\b",
    "manchetes": r"manchete\w*|not[íi]cia\w*",
}

# Negacao GENERICA da camada, sem nomear bloco ("a camada fundamental nao
# veio"). E' o caso do incidente AMD, e continua valendo quando QUALQUER
# bloco esta' presente.
_FUNDAMENTO_GENERICO = r"fundament\w+|avalia[cç][aã]o"

# O SUJEITO da negacao tem que ser a camada fundamental. Sem isto, "o RSI nao
# estava disponivel" -- frase sobre outro dado -- viraria apontamento.
_FALA_DO_FUNDAMENTO = (
    r"fundament\w+|valuation|avalia[cç][aã]o|alvos?\s+d\w+\s+analist\w+"
    r"|pre[cç]o[- ]alvo|consenso|m[uú]ltiplos?|\bdcf\b|p/l\b|p/vp\b")

_FALA_DA_FAIXA = r"faixa|52\s*semanas|intervalo\s+anual|amplitude\s+anual|range"

# "chegou ao evento com um run-up de X%" -- `runup_atual_ex_evento_pct` é o
# run-up de HOJE, não o que o papel tinha ao CHEGAR num balanço que já passou.
_EVENTO = r"(?:evento|balan[çc]o|resultado|an[úu]ncio|divulga[çc][ãa]o)"

# Como o texto diz "o papel CHEGOU ao balanço com tal run-up".
#
# As tres primeiras formas vieram do incidente SMCI. As duas ultimas foram
# acrescentadas depois de o mesmo erro escapar em ARM e NVDA (26/08/2026) por
# construcoes que ninguem tinha previsto:
#
#   ARM : "havia se valorizado 11,64% nos 21 pregoes que o ANTECEDERAM"
#   NVDA: "ANTES DESTE EVENTO, o papel apresentava um run-up de 8,14%
#          nos 21 pregoes anteriores"
#
# Duas ocorrencias em telas diferentes no mesmo dia dizem que nao era um
# acidente de redacao: o modelo tem varias maneiras de dizer "chegou", e a
# lista precisa cobrir a IDEIA, nao as tres frases que ja' vimos.
#
# "nos N pregoes anteriores" sozinho fica DE FORA de proposito: anteriores a
# QUE? O run-up atual tambem e' medido sobre os 21 pregoes anteriores -- a
# HOJE --, e essa frase esta certa. So' cai quando ha' ancora no EVENTO: o
# pronome que retoma o balanco ("que O antecederam") ou "antes deste evento".
# Sem essa exigencia, a checagem apontaria contra a redacao correta.
_CHEGADA_AO_EVENTO = (rf"cheg\w+\s+(?:ao|no|até\s+o)\s+{_EVENTO}|"
                      rf"veio\s+(?:ao|para\s+o)\s+{_EVENTO}|"
                      rf"antes\s+d[oe]\s+{_EVENTO}\s+com|"
                      # "nos 21 pregoes que O antecederam" -- o pronome
                      # retoma o balanco, entao a ancora existe.
                      r"preg[õo]es\s+que\s+(?:o|lhe|a)\s+anteced\w+|"
                      rf"anteced\w+\s+(?:o\s+|este\s+|esse\s+)?{_EVENTO}|"
                      # "antes deste evento", "antes do balanco" (sem exigir
                      # o "com" que a forma antiga pedia logo em seguida).
                      rf"antes\s+d(?:o|este|esse|aquele)\s+{_EVENTO}|"
                      rf"pr[ée]-?\s?(?:balan[çc]o|earnings|{_EVENTO})\b")

# Verbos que ATRIBUEM um movimento ao balanço. É o que separa "reagiu com X%"
# (atribuição) de "a reação ocorreu em 2026-08-19" (circunstância).
_ATRIBUI_REACAO = r"(?:reag\w+|rea[cç][ãa]o\s+(?:foi|de|veio|saiu|ficou))"

# Numero atribuido a' "reacao" e colado a "dia do anuncio" -- a redacao
# tipica do erro: "a reacao ... foi de X% no dia do anuncio". Prosa_sa e'
# sem acento e minuscula, entao o literal fica sem acento de proposito.
# Abaixo disso o SYSTEM pede o N declarado ao citar a estatistica de
# reacao -- mesmo limiar do N_EVENTOS_DECLARAR da tela Reacao a Earnings.
N_EVENTOS_DECLARAR_ANALISE = 5

_REACAO_DIA_DO_ANUNCIO = re.compile(
    r"(?:reacao[^.\n]{0,40}?foi\s+de|reagiu\s+(?:com|em)?)\s*"
    r"([+-]?\d+(?:[.,]\d+)?)\s*%"
    r"[^.\n]{0,20}?dia\s+d[oe]\s+anuncio",
    re.IGNORECASE)

# Conecta dois números como se fossem a mesma coisa/fase -- é a palavra que
# faz "-1,23% ... -1,49% observado agora" virar comparação, e não duas
# menções soltas do check 17 (pré-reação x média histórica do balde).
_COMPARA_AO_ATUAL = (r"pr[óo]xim[oa]\s+(?:a|ao|d[ao]|d[eo])|similar\s+a|"
                     r"parecid[oa]\s+(?:a|com)|compat[íi]vel\s+com|"
                     r"em\s+linha\s+com|perto\s+d[eo]|na\s+mesma\s+ordem")

# "a reação média ... no dia do anúncio" -- rotula a ESTATÍSTICA AGREGADA
# (close_pct_mean/gap_pct_mean) como se pertencesse à sessão do anúncio.
# Para AMC essas médias vêm da sessão SEGUINTE (a própria tabela marca com
# ◂); o número costuma estar certo, o QUANDO é que está errado. Duas ordens
# de palavra porque o modelo escreve as duas ("a reação média ... no dia do
# anúncio" e "no dia do anúncio, a reação média ...").
_REACAO_MEDIA_E_ANUNCIO = (
    r"rea[cç][ãa]o\s+m[ée]dia[^.\n]{0,40}?dia\s+d[oe]\s+anuncio"
    r"|dia\s+d[oe]\s+anuncio[^.\n]{0,40}?rea[cç][ãa]o\s+m[ée]dia"
)

# O nome do run-up escrito por extenso. Quando ele aparece na frase, o texto
# está DISTINGUINDO os dois conceitos, e citar o número do run-up ali é a
# redação que o SYSTEM pede -- não o erro que a checagem procura.
_NOMEIA_O_RUNUP = r"run[-\s]?up|corrida|acumulad\w+\s+antes|antes\s+do\s+balan[cç]o"


def _secao_presente(prosa: str, secao: str) -> bool:
    """O título tem que ser uma LINHA de cabeçalho, não uma substring.

    A versão anterior procurava `"## sintese" in prosa`, o que aceitava
    "## Síntese preliminar descartada" como se fosse a seção pedida e recusava
    "**Síntese**". Aqui o título pode vir com qualquer nível de `#`, em
    negrito, ou numerado -- mas tem que terminar ali."""
    alvo = re.escape(sem_acento(secao))
    padrao = (rf"(?m)^\s*(?:#{{1,6}}\s*|\*\*\s*)?(?:\d+[.)]\s*)?"
              rf"{alvo}\s*(?:\*\*)?\s*:?\s*$")
    return bool(re.search(padrao, sem_acento(prosa)))


def _primeiro_valor(*valores):
    """O primeiro numérico da lista, distinguindo 0.0 de ausente.

    `technicals or snapshot` tratava momentum 0.0 como ausente e caía para o
    outro painel -- checando o texto contra um número que não era o do painel
    técnico. Zero é valor válido de momentum."""
    for v in valores:
        if num_finito(v) is not None:
            return num_finito(v)
    return None


# ── Beta lido como razão de volatilidade ────────────────────────────────────
#
# Duas telas, o mesmo erro conceitual (26/08/2026):
#
#   ARM : "o beta [...] e' de 1,2861, indicando que o papel tem uma
#          volatilidade 28,61% MAIOR que a do setor"
#   NVDA: "O beta setorial de 0,6407 indica que NVDA tende a ser MENOS
#          volatil que o benchmark SMH"
#
# Beta e' inclinacao de regressao -- `cov(ticker, bench) / var(bench)`, em
# get_scenario_params.py. Em termos de volatilidade, beta = rho * (sigma_a /
# sigma_m): so' quando a correlacao e' 1 ele iguala a razao de volatilidades.
# Um papel pode ter beta abaixo de 1 e volatilidade propria alta, que e'
# exatamente o caso da NVDA -- 37% ao ano com beta 0,64.
#
# E o dado certo esta' no MESMO payload, ao lado: `volAnnual`. Dizer "menos
# volatil" a partir do beta e' trocar um numero disponivel por uma inferencia
# errada.
_BETA = r"\bbeta\b"

# O que separa a conclusao errada da mencao legitima e' o CONECTIVO. "o beta
# e' 0,64 e a volatilidade anual e' 37%" poe os dois lado a lado sem concluir
# nada -- e e' a redacao correta. So' cai quando o texto DERIVA volatilidade
# do beta.
_DERIVA_VOLATILIDADE = (
    r"(?:indica\w*|significa\w*|sugere\w*|implica\w*|quer\s+dizer|"
    r"ou\s+seja|portanto|logo|isto\s+[ée]|traduz\w*|reflete|"
    r"mostra\s+que|revela\w*)[^.;]{0,60}?(?:volat|vol[áa]til)"
)

# "beta 0,64 -> menos volatil", sem conectivo nenhum no meio.
_BETA_VOLATIL_DIRETO = r"\bbeta\b[^.;]{0,80}?(?:mais|menos)\s+vol[áa]til"


# ── Significância afirmada sem o campo que a banca ──────────────────────────
#
# Incidente real (NVDA, 26/08/2026): "Nota-se uma correlacao forte e
# ESTATISTICAMENTE SIGNIFICATIVA de 0,92 entre o run-up pre-balanco e a reacao
# subsequente".
#
# O payload desta tela traz `corr_runup_reacao` e mais nada: `aplicar_holm`
# nao roda aqui, porque a correcao de multiplos so' existe ENTRE tickers e a
# Analise Rapida olha um papel so'. Ou seja, nao ha' campo de significancia no
# dado -- o modelo afirmou uma propriedade que ninguem calculou.
#
# `LEITURA_CORRELACAO_SEM_SUPORTE` ja' existia para isto, no
# `reacao_earnings_validator`. E so' la'. E' a quarta vez neste dia que uma
# defesa mora num validador so' e a tela vizinha nasce sem ela -- as outras
# tres foram MOEDA_ERRADA (#405), mask_sensitive_data (#409) e a checagem de
# distancia da media (#411).
_AFIRMA_SIGNIFICANCIA = (
    r"estatisticamente\s+(?:significativ\w*|relevante)|"
    r"significância\s+estat[íi]stica|signific[âa]ncia\s+estat[íi]stica|"
    r"p\s*<\s*0[.,]0\d|com\s+signific[âa]ncia"
)


# ── Contagem do balde "chegou esticado" ─────────────────────────────────────
#
# Incidente real (ARM, 26/08/2026): "Historicamente, em 3 dos 7 eventos onde o
# papel chegou esticado, ele reagiu com uma media de 1,37% de alta."
#
# O dado diz outra coisa: 3 eventos esticados, dos quais 2 CAIRAM, media
# +1,37%. O modelo trocou o par de numeros e, de quebra, transformou "2 de 3
# cairam" em "reagiu com media de alta" -- some a informacao de que a maioria
# caiu, que e' a unica que muda decisao.
#
# A auditoria propos "mandar agregados pre-calculados para o modelo nao contar
# sozinho". Eles JA' VAO: `esticado_n`, `esticado_caiu_n` e
# `esticado_reacao_media` estao no `summary.runup` que o payload carrega
# inteiro. O modelo tinha os tres na mao e contou assim mesmo. Entao o que
# falta nao e' o dado, e' a CONFERENCIA.
#
# Dois pares sao legitimos e a checagem aceita os dois, porque as duas leituras
# aparecem em texto correto:
#
#     (esticado_caiu_n, esticado_n)  -- "2 de 3 esticados cairam"
#     (esticado_n,      n_events)    -- "3 dos 8 eventos chegaram esticados"
_PAR_CONTADO = re.compile(
    r"(\d+)\s+d(?:e|os|as)\s+(\d+)\s+"
    r"(?:eventos?|balan[çc]os?|casos?|resultados?|trimestres?)",
    re.IGNORECASE)


def validar_analise(texto, dados=None) -> list:
    """[{nivel, codigo, mensagem}] -- vazio quando nada destoa.

    "ERRO" é afirmação que o dado CONTRADIZ ou regra dura desobedecida;
    "AVISO" é o que falta declarar. Só ERRO justifica gastar uma retentativa
    de LLM."""
    achados = []

    def add(nivel, codigo, mensagem):
        achados.append({"nivel": nivel, "codigo": codigo, "mensagem": mensagem})

    # ── 0. o texto chegou inteiro? ──────────────────────────────────────────
    #
    # Resposta vazia devolvia [] -- que quem chama lê como "nada destoa".
    # Falha de geração era publicada como análise conferida.
    ok, motivo = texto_utilizavel(texto)
    if not ok:
        add("ERRO", "ANALISE_TEXTO_VAZIO",
            f"{motivo} — não há análise para publicar.")
        return achados

    prosa = sem_blocos_de_codigo(texto)
    prosa_sa = sem_acento(prosa)
    prosa_min = minusculas(prosa)

    # ── 0b. o texto NEGA dado que recebeu ───────────────────────────────────
    #
    # Incidente real (AMD, 26/08/2026), publicado sem nenhum apontamento. A
    # linha de fontes da tela dizia
    #
    #     "camada fundamental: alvos de analistas (yfinance),
    #      valuation/DCF (FMP), notícias do feed"
    #
    # -- as TRÊS chegaram -- e a seção Fundamento e valuation dizia
    #
    #     "Informações fundamentais e de valuation, como alvos de analistas e
    #      métricas de avaliação, não estavam disponíveis para AMD nesta
    #      análise."
    #
    # É o inverso exato do caso SNDK do mesmo dia: lá o dado faltava e a tela
    # não dizia por quê; aqui o dado veio e o texto o nega. Os dois saem da
    # mesma lacuna -- ninguém conferia as afirmações do texto sobre a
    # DISPONIBILIDADE do dado, só sobre o valor dele.
    #
    # E negar dado presente é pior que omitir: o leitor que vê "não estava
    # disponível" para de procurar. A informação estava a uma seção de
    # distância, e o texto o convenceu de que não existia.
    #
    # O prompt manda dizer em uma linha quando a camada não vem ("Use só a
    # camada que veio: sem valuation nem alvos, diga em uma linha que a
    # fundamental não estava disponível e siga"). A frase é legítima --
    # quando é verdade. Esta checagem é o que separa os dois casos.
    fundamento = dic(caminho(dados, "_fundamento"))
    presentes = [rotulo for chave, rotulo in _BLOCOS_FUNDAMENTAIS
                 if fundamento.get(chave)]
    chaves_presentes = [chave for chave, _ in _BLOCOS_FUNDAMENTAIS
                        if fundamento.get(chave)]
    if presentes:
        for frase in frases(prosa_sa):
            if not re.search(_NEGA_DISPONIBILIDADE, frase):
                continue
            if not re.search(_FALA_DO_FUNDAMENTO, frase):
                continue
            # A frase nega a camada em geral, ou nomeia um bloco que VEIO?
            # Frase que só nomeia bloco ausente ("a FMP não forneceu o DCF")
            # está certa e não pode virar ERRO por causa de outro bloco.
            nega_generico = bool(re.search(_FUNDAMENTO_GENERICO, frase))
            nomeados = [c for c, termos in _TERMOS_DO_BLOCO.items()
                        if re.search(termos, frase)]
            presentes_na_frase = [c for c in nomeados if c in chaves_presentes]
            if not nega_generico and not presentes_na_frase:
                continue
            citados = ([r for c, r in _BLOCOS_FUNDAMENTAIS if c in presentes_na_frase]
                       or presentes)
            add("ERRO", "ANALISE_NEGA_DADO_PRESENTE",
                f"diz que a camada fundamental não veio, mas o payload traz "
                f"{', '.join(citados)} — quem lê isso para de procurar um "
                f"dado que está na mão. "
                f"Trecho: “{frase.strip()[:120]}”.")
            break

    # ── 1. as seis seções ───────────────────────────────────────────────────
    faltando = [s for s in SECOES_OBRIGATORIAS if not _secao_presente(prosa, s)]
    if faltando:
        add("ERRO", "ANALISE_SECAO_FALTANDO",
            "faltam as seções: " + ", ".join(faltando)
            + (" — a Síntese é a primeira a se perder quando o texto estica."
               if "Síntese" in faltando else "."))

    # ── 2. moeda ────────────────────────────────────────────────────────────
    #
    # A regra mora em `validador_nucleo` desde 26/08/2026: um veredito de ARM
    # saiu com "R$ 249,57" e passou, porque a checagem existia só AQUI. Uma
    # cópia por validador é uma chance de a próxima tela ficar sem.
    frase_moeda = frase_com_moeda_errada(prosa)
    if frase_moeda:
        add("ERRO", "ANALISE_MOEDA_ERRADA",
            "usa R$ para o preço do ativo — os papéis são listados nos EUA e o "
            f"prompt manda não converter; escreva US$. "
            f"Trecho: “{frase_moeda.strip()[:120]}”.")

    # ── 3. momentum anualizado descrito como período ────────────────────────
    #
    # Número certo virando afirmação falsa: `momentumAnnualPct` é taxa
    # ANUALIZADA extrapolada de `lookbackDays`. "106% em 90 dias" é o mesmo
    # número dizendo outra coisa.
    #
    # A frase tem que NOMEAR o momentum. Sem isso, "caiu 106,5% em 3 dias" --
    # descrição legítima de preço que por acaso bate com o valor do campo --
    # virava ERRO. Coincidência numérica não é afirmação errada.
    momentum = _primeiro_valor(caminho(dados, "technicals").get("momentumAnnualPct"),
                               caminho(dados, "snapshot").get("momentumAnnualPct"))
    if momentum is not None:
        for frase in frases(prosa_sa):
            # O texto que JÁ declara a taxa como anualizada está obedecendo.
            if re.search(r"anualizad\w*|ao\s+ano|a\.?a\.?\b", frase):
                continue
            if not cita_numero(frase, abs(momentum), inteiro_ok=True):
                continue
            if re.search(r"\d\s*%\s*(?:em|nos?|durante)\s+\d+\s*"
                         r"(?:dia|preg|seman|m[êe]s|mes)\w*", frase):
                add("ERRO", "ANALISE_MOMENTUM_COMO_PERIODO",
                    f"apresenta o momentum de {momentum}% como variação de um "
                    f"período — o campo é taxa ANUALIZADA extrapolada da "
                    f"janela, não o que o papel fez nela. "
                    f"Trecho: “{frase.strip()[:120]}”.")
                break

    # ── 4. divergência de preço não declarada ───────────────────────────────
    preco = caminho(dados, "precoAtual")
    por_painel = caminho(preco, "porPainel")
    valores = sorted(v for v in (num_finito(x) for x in por_painel.values())
                     if v is not None)
    if num_finito(preco.get("divergenciaPct")) and len(valores) >= 2:
        extremos = (valores[0], valores[-1])
        ausentes = [v for v in extremos
                    if not cita_numero(prosa, v, inteiro_ok=True)]
        if ausentes:
            add("ERRO", "ANALISE_DIVERGENCIA_OMITIDA",
                f"os painéis divergem {preco['divergenciaPct']}% e o texto não "
                f"traz os dois preços (US$ {extremos[0]:.2f} e "
                f"US$ {extremos[1]:.2f}) — sem eles o leitor não sabe qual "
                f"indicador está apoiado em qual preço.")

    # ── 5. recomendação de compra/venda ─────────────────────────────────────
    #
    # A janela de negação é a FRASE inteira, não 40 caracteres: "Dada a análise
    # atual e todos os indicadores, não é hora de comprar" tinha o "não" fora
    # da janela e virava ERRO.
    for frase in frases(prosa_sa):
        if any(re.search(neg, frase) for neg in _META_RECUSA):
            continue
        alvo = next((p for p in _RECOMENDA
                     if re.search(p, frase) and not afirmacao_negada(frase, p)),
                    None)
        if alvo:
            add("ERRO", "ANALISE_RECOMENDACAO",
                "recomenda comprar ou vender — o prompt pede cenários e níveis "
                "de invalidação; a decisão é do leitor. "
                f"Trecho: “{frase.strip()[:120]}”.")
            break

    # ── 6. bandas estatísticas tratadas como nível técnico ──────────────────
    #
    # A primeira versão exigia só CO-OCORRÊNCIA de "R1" e "resistência" na
    # mesma frase -- e o SYSTEM manda o modelo escrever exatamente a distinção,
    # então obedecer e errar davam o mesmo apontamento. A segunda olhava a
    # negação apenas DENTRO do trecho casado, e "não é o suporte R1" casava
    # como "suporte r1", sem o "não". Agora a negação é avaliada na FRASE.
    for frase in frases(prosa_min):
        achou = None
        # A negação é avaliada contra `_NIVEL`, não contra o padrão inteiro:
        # em "S1 e S2 NÃO SÃO suporte" o "não" está no meio da construção, e
        # procurá-lo antes do padrão (que começa na banda) nunca o acharia.
        if (afirmacao_negada(frase, _NIVEL)
                or re.search(_ROTULA_A_BANDA, frase)
                or re.search(_NIVEL_QUALIFICADO, frase)):
            continue
        for padrao in (_BANDA_VIRA_NIVEL, _BANDA_ELIPSE, _NIVEL_VIRA_BANDA,
                       _NIVEL_ARROLA_BANDA, _NIVEL_E_A_BANDA):
            m = re.search(padrao, frase)
            if m:
                achou = m
                break
        if achou:
            add("ERRO", "ANALISE_BANDA_COMO_NIVEL_TECNICO",
                "trata R1/R2/S1/S2 como suporte ou resistência do gráfico — "
                "são bandas de volatilidade (preço ± reação média a earnings). "
                f"Trecho: “{achou.group(0).strip()}”.")
            break

    # ── 7. balanço já ocorrido escrito no futuro ────────────────────────────
    #
    # Incidente real (NBIS, 17/08/2026): a janela de run-up engolia o pregão de
    # reação e o texto dizia que o papel "chega esticado ao balanço" -- de um
    # balanço que já tinha acontecido.
    runup = caminho(dados, "reaction", "summary", "runup")
    if runup.get("janela_contem_earnings"):
        pregoes = runup.get("pregoes_desde_earnings", "?")

        # PRESENTE ou FUTURO sobre um balanço que já passou. O lookahead
        # exclui as formas de PASSADO -- "chegou/chegava/chegara esticado"
        # descrevendo eventos HISTÓRICOS é a redação correta, e a primeira
        # versão apontava contra ela (falso positivo do WOLF, 26/08/2026).
        # Sobre `prosa_min` (COM acento) de propósito: sem ele "chegará"
        # (futuro, erro) e "chegara" (mais-que-perfeito, correto) viram a
        # mesma palavra, e excluir uma apagaria a outra.
        if re.search(r"\bcheg(?!ou\b|aram\b|ado\b|ada\b|ados\b|adas\b|ava\b|"
                     r"avam\b|ara\b|aras\b)\w*\s+esticad|"
                     r"\bchega\w*\s+ao\s+balan[çc]o", prosa_min):
            add("ERRO", "ANALISE_BALANCO_NO_FUTURO",
                f"escreve no presente ou futuro sobre um balanço que JÁ ocorreu "
                f"há {pregoes} pregão(ões) — o run-up bruto inclui o próprio "
                f"salto do evento.")

        # O run-up de HOJE atribuído à CHEGADA num balanço que já passou.
        #
        # Incidente real (SMCI, 26/08/2026): "a ação chegou ao evento com um
        # run-up de 32,46%, considerada esticada". Os 32,46% são
        # `runup_atual_ex_evento_pct` -- o run-up de AGORA, 11 pregões DEPOIS
        # do balanço. O run-up com que ela chegou em 2026-08-11 foi +11,13%.
        #
        # A checagem 7 acima excusa o passado ("chegou esticado") porque
        # descrever histórico é a redação certa. Aqui o tempo verbal está
        # certo e o NÚMERO é que é de outro momento.
        atual = num_finito(runup.get("runup_atual_ex_evento_pct"))
        if atual is not None:
            for frase in frases(prosa_sa):
                if not re.search(_CHEGADA_AO_EVENTO, frase):
                    continue
                if not _cita_percentual(frase, abs(atual)):
                    continue
                add("ERRO", "ANALISE_RUNUP_ATUAL_COMO_CHEGADA",
                    f"diz que o papel CHEGOU ao balanço com {atual}%, mas esse "
                    f"é o run-up de AGORA — medido {pregoes} pregão(ões) "
                    f"DEPOIS do evento. O run-up de chegada está na tabela de "
                    f"eventos, não neste campo. "
                    f"Trecho: “{frase.strip()[:120]}”.")
                break

        # O esticamento atribuído ao PRÓXIMO balanço. Foi assim que o erro
        # real escapou em WOLF: "o preço atual está esticado em relação ao
        # PRÓXIMO balanço, pois nos 4 pregões desde o último evento...".
        #
        # As duas metades têm que estar LIGADAS na mesma frase. O braço solto
        # `pr[oó]ximo\s+balan[cç]o` transformava "o próximo balanço sai em
        # novembro" -- frase correta e informativa -- em ERRO.
        for frase in frases(prosa_sa):
            if not re.search(r"pr[oó]xim\w+\s+(?:balan[cç]o|resultado|evento)|"
                             r"balan[cç]o\s+que\s+vem", frase):
                continue
            if re.search(r"esticad\w*|run[-\s]?up|descontad\w*", frase):
                add("ERRO", "ANALISE_ESTICAMENTO_NO_PROXIMO_BALANCO",
                    f"pendura o esticamento no PRÓXIMO balanço, mas ele vem do "
                    f"que ocorreu há {pregoes} pregão(ões) — use "
                    f"`runup_atual_ex_evento_pct` e escreva no passado. "
                    f"Trecho: “{frase.strip()[:120]}”.")
                break

    # ── 8. direção invertida contra média móvel ─────────────────────────────
    #
    # Incidente real (ADI, 26/08/2026), publicado sem nenhum apontamento: "o
    # preço de US$ 373,66 está apenas 0,72% ACIMA da MM20 (US$ 376,36)". A
    # magnitude estava certa e o sinal invertido -- 373,66 é MENOR que 376,36.
    # A frase até se contradizia sozinha ("acima da MM20, MAS abaixo da MM50",
    # como se fosse contraste), e ainda assim passou: nenhuma checagem olhava
    # a DIREÇÃO, só os números.
    #
    # A guarda contra o falso positivo é a magnitude. Só aponta quando o
    # percentual citado BATE com a distância real entre preço e nível — o que
    # confirma que a frase fala dessa comparação e não de outra. "A MM50 está
    # 12% acima da MM200" não casa, porque 12 não é a distância do PREÇO à
    # MM200, e é assim que o sujeito da frase fica implicitamente checado.
    preco_atual = _primeiro_valor(caminho(dados, "precoAtual").get("valor"),
                                  caminho(dados, "snapshot").get("price"))
    if preco_atual:
        for frase in frases(prosa_sa):
            achado = _direcao_contradita(frase, preco_atual, dados)
            if achado:
                rotulo, citado, dito, valor, real = achado
                add("ERRO", "ANALISE_DIRECAO_INVERTIDA",
                    f"diz que o preço está {citado:.2f}% {dito} da {rotulo} "
                    f"(US$ {valor:.2f}), mas US$ {preco_atual:.2f} está "
                    f"{'acima' if real > 0 else 'abaixo'} dela — magnitude "
                    f"certa, sinal trocado. "
                    f"Trecho: “{frase.strip()[:120]}”.")
                break

    # ── 8b. distância à média medida contra o PREÇO ─────────────────────────
    #
    # Ver `_base_da_media_trocada`: a checagem 8 confere o SINAL e aceita as
    # duas bases de propósito; esta confere a BASE com o sinal já certo. Em
    # ARM as duas passaram lado a lado -- "3,78% abaixo da MM20" tem a direção
    # correta (o preço está mesmo abaixo) e a base errada, então a 8 não podia
    # apontar e ninguém mais olhava.
    if preco_atual:
        vistas: set = set()
        for frase in frases(prosa_sa):
            for rotulo, citado, nivel, correto in \
                    _base_da_media_trocada(frase, preco_atual, dados):
                if rotulo in vistas:
                    continue
                vistas.add(rotulo)
                add("ERRO", "ANALISE_DISTANCIA_DA_MEDIA",
                    f"diz {citado:.2f}% em relação à {rotulo} "
                    f"(US$ {nivel:.2f}), mas esse número vem de dividir pelo "
                    f"PREÇO. Medido contra a própria {rotulo}, US$ "
                    f"{preco_atual:.2f} está {correto:+.2f}% — que é o número "
                    f"que o painel Técnica mostra. "
                    f"Trecho: “{frase.strip()[:120]}”.")

    # ── 9. posição na faixa de 52 semanas ───────────────────────────────────
    #
    # Incidente real (SMCI, 26/08/2026): "o ativo negocia na METADE SUPERIOR da
    # sua faixa anual", com preço US$ 37,88 numa faixa de 19,48 a 58,78 -- ou
    # seja, a 46,8% dela, na metade de BAIXO.
    #
    # O texto tinha os dois números certos na frase anterior ("55,17% da máxima
    # e 48,57% acima da mínima") e derivou deles a conclusão oposta. É o padrão
    # do dia inteiro: o número está certo e a leitura, invertida.
    faixa_lo = num_finito(caminho(dados, "snapshot").get("yearLow"))
    faixa_hi = num_finito(caminho(dados, "snapshot").get("yearHigh"))
    if preco_atual and faixa_lo and faixa_hi and faixa_hi > faixa_lo:
        posicao = (preco_atual - faixa_lo) / (faixa_hi - faixa_lo)
        for frase in frases(prosa_sa):
            if not re.search(_FALA_DA_FAIXA, frase):
                continue
            diz_cima = any(re.search(x, frase) for x in _METADE_DE_CIMA)
            diz_baixo = any(re.search(x, frase) for x in _METADE_DE_BAIXO)
            if diz_cima == diz_baixo:
                continue  # nada dito, ou os dois (comparação, não afirmação)
            if diz_cima and posicao < 0.5 or diz_baixo and posicao >= 0.5:
                add("ERRO", "ANALISE_POSICAO_NA_FAIXA",
                    f"diz que o papel está na metade "
                    f"{'superior' if diz_cima else 'inferior'} da faixa, mas "
                    f"US$ {preco_atual:.2f} está a {posicao * 100:.1f}% do "
                    f"intervalo US$ {faixa_lo:.2f}–{faixa_hi:.2f} — metade "
                    f"{'inferior' if posicao < 0.5 else 'superior'}. "
                    f"Trecho: “{frase.strip()[:120]}”.")
                break

        # ── 9b. a distância à máxima/mínima medida contra o PREÇO ───────────
        #
        # Incidente real (SNDK, 26/08/2026), publicado com a caixa apontando
        # OUTRA coisa: "está 58,51% abaixo da máxima de 52 semanas (US$
        # 2354,39) e 96,81% acima da mínima (US$ 47,40)". Com o papel a US$
        # 1485,30, o certo é -36,91% e +3033,54%. Os dois números saíram de
        # dividir pelo PREÇO em vez de pela referência.
        #
        # Por que isto merece checagem própria, e não uma linha em
        # `_REFERENCIAS`: `_bate_a_magnitude` aceita as DUAS bases de
        # propósito, porque preço e média móvel andam perto e a convenção
        # não muda o número o bastante para valer uma briga. Com a mínima
        # anual não é assim -- US$ 1485,30 é 31 vezes US$ 47,40. Trocar a
        # base transforma +3033% em +96,81%.
        #
        # E é justamente aí que mora o veneno: dividir pelo preço produz um
        # número que NUNCA passa de 100%. "96,81% acima da mínima" se lê como
        # "quase no teto da faixa" -- plausível, arredondado, e a 3000% da
        # verdade. Um erro que se disfarça de número bem-comportado é pior
        # que um número absurdo, porque o absurdo o leitor pega sozinho.
        #
        # A guarda contra falso positivo é a mesma da checagem 8: só aponta
        # quando o citado bate com a conta ERRADA. Número que não bate com
        # nenhuma das duas é outra conversa, e o silêncio aqui é de propósito.
        vistos: set = set()
        for frase in frases(prosa_sa):
            for rotulo, citado, referencia, correto in \
                    _bases_da_distancia_trocadas(frase, preco_atual,
                                                 faixa_lo, faixa_hi):
                if rotulo in vistos:
                    continue
                vistos.add(rotulo)
                add("ERRO", "ANALISE_DISTANCIA_DA_FAIXA",
                    f"diz {citado:.2f}% em relação à {rotulo} "
                    f"(US$ {referencia:.2f}), mas esse número vem de dividir "
                    f"pelo PREÇO. Medido contra a própria {rotulo}, US$ "
                    f"{preco_atual:.2f} está {correto:+.2f}%. "
                    f"Trecho: “{frase.strip()[:120]}”.")

    # ── 10. níveis descritos fora de ordem ──────────────────────────────────
    #
    # Incidente real (INTC, 26/08/2026): "encontra seu primeiro nível técnico
    # significativo na MM20 a US$ 95,78 (+10,33%), SEGUIDA de perto pela banda
    # R1 a US$ 94,97 (+9,4%)". Subindo de US$ 86,81 você encontra a R1 antes
    # da MM20 -- a ordem está invertida.
    #
    # O prompt entrega a lista de níveis JÁ ORDENADA justamente para o modelo
    # não ter de ordenar ("ele descreve uma lista já ordenada, não ordena"), e
    # foi essa etapa que a leitura refez errado.
    for frase in frases(prosa_sa):
        fora = _ordem_invertida(frase, preco_atual)
        if fora:
            antes, depois, unidade = fora
            escrever = ((lambda v: f"{v:+.2f}%") if unidade == "%"
                        else (lambda v: f"US$ {abs(v):.2f} "
                                        f"({'acima' if v > 0 else 'abaixo'})"))
            add("ERRO", "ANALISE_ORDEM_DOS_NIVEIS",
                f"descreve o nível a {escrever(antes)} do preço como vindo "
                f"ANTES do que está a {escrever(depois)}, mas este é o mais "
                f"próximo — a lista de níveis do JSON já vem ordenada. "
                f"Trecho: “{frase.strip()[:120]}”.")
            break

    # ── 11. estatística de earnings com o rótulo de outra ───────────────────
    #
    # Incidente real (INTC, 26/08/2026), publicado sem apontamento: "um
    # descolamento (gap) médio de 8,25% na abertura". Os oito gaps da tabela
    # ficam todos abaixo de 2,2% e a média absoluta é 0,83% -- 8,25% é outra
    # linha do resumo com o rótulo do gap.
    #
    # O erro típico não é inventar número, é PEGAR O NÚMERO CERTO DE OUTRO
    # CAMPO. Por isso a mensagem diz qual campo o valor realmente é: sem isso,
    # quem lê o apontamento não sabe se corrige o número ou o rótulo.
    resumo = caminho(dados, "reaction", "summary")
    if resumo:
        # Sobre `prosa_min` (COM acento) porque a separação por oração divide
        # em " e ": sem acento "é" vira "e" e "o gap médio É de 15,09%" seria
        # partido no meio, deixando o número órfão do seu rótulo.
        for frase in frases(prosa_min):
            achado = _estatistica_trocada(frase, resumo)
            if achado:
                rotulo, citado, esperados, bate_com = achado
                add("ERRO", "ANALISE_ESTATISTICA_TROCADA",
                    f"atribui {citado:.2f}% ao {rotulo}, mas o resumo traz "
                    + " e ".join(f"{v:.2f}%" for v in esperados)
                    + (f" — o número citado é o campo `{bate_com}`"
                       if bate_com else " — o número não está no resumo")
                    + f". Trecho: “{frase.strip()[:120]}”.")
                break

    # ── 12. run-up apresentado como reação ──────────────────────────────────
    #
    # O pior erro da rodada de WOLF, e o que nenhum validador pegava: "o papel
    # reagiu com 14,92% de alta", quando 14,92% é o run-up EX-EVENTO e a
    # reação do dia foi -7,53%. Sinal invertido, com cara de fato apurado.
    #
    # Três guardas contra o falso positivo, todas vindas de rodadas reais:
    #   - o número tem que vir com % (senão "a reação foi medida 14,92 horas
    #     depois" casava);
    #   - o verbo tem que ATRIBUIR a reação, não só mencioná-la ("a reação
    #     ocorreu em 2026-08-19" é circunstância);
    #   - a frase não pode NOMEAR o run-up -- "Reação foi -7,53% após run-up de
    #     14,92%" é exatamente a redação que o SYSTEM pede.
    ex_evento = num_finito(runup.get("runup_atual_ex_evento_pct"))
    if ex_evento is not None:
        for frase in frases(prosa_sa):
            if not re.search(_ATRIBUI_REACAO, frase):
                continue
            if re.search(_NOMEIA_O_RUNUP, frase):
                continue
            if not re.search(r"\d\s*%", frase):
                continue
            if _cita_percentual(frase, abs(ex_evento)):
                add("ERRO", "ANALISE_RUNUP_COMO_REACAO",
                    f"apresenta o run-up de {ex_evento}% como se fosse a REAÇÃO "
                    f"ao balanço — são coisas opostas: o run-up é o que veio "
                    f"ANTES, e citá-lo como reação pode inverter o sinal do que "
                    f"aconteceu. Trecho: “{frase.strip()[:120]}”.")
                break

    # ── 12. beta lido como razão de volatilidade ────────────────────────────
    for frase in frases(prosa_sa):
        if not re.search(_BETA, frase):
            continue
        alvo = next((a for a in (_DERIVA_VOLATILIDADE, _BETA_VOLATIL_DIRETO)
                     if re.search(a, frase)), None)
        if not alvo or afirmacao_negada(frase, alvo):
            continue
        add("ERRO", "ANALISE_BETA_COMO_VOLATILIDADE",
            "deriva volatilidade do beta, mas beta é inclinação de regressão "
            "contra o benchmark (cov/var), não razão de volatilidades — só "
            "quando a correlação é 1 os dois coincidem. A volatilidade está "
            "no próprio payload, em `volAnnual`. "
            f"Trecho: “{frase.strip()[:120]}”.")
        break

    # ── 13. significância afirmada sem o campo que a banca ──────────────────
    runup_corr = caminho(dados, "reaction", "summary", "runup")
    if num_finito(runup_corr.get("corr_runup_reacao")) is not None \
            and not booleano(runup_corr.get("corr_sobrevive")):
        for frase in frases(prosa_sa):
            if not re.search(r"correla[cç][ãa]o", frase):
                continue
            if not afirmado_sem_negacao(frase, _AFIRMA_SIGNIFICANCIA):
                continue
            n = runup_corr.get("corr_n")
            add("ERRO", "ANALISE_SIGNIFICANCIA_SEM_CAMPO",
                "chama a correlação de estatisticamente significativa, mas o "
                "payload traz só o coeficiente"
                + (f" (n = {n})" if n else "")
                + " — não há p-valor nem `corr_sobrevive` nesta tela, então a "
                "afirmação não vem do dado. "
                f"Trecho: “{frase.strip()[:120]}”.")
            break

    # ── 14. contagem do balde "chegou esticado" ─────────────────────────────
    resumo_reacao = caminho(dados, "reaction", "summary")  # reusado no check 16
    ru_balde = dic(resumo_reacao.get("runup"))
    esticado_n = num_finito(ru_balde.get("esticado_n"))
    caiu_n = num_finito(ru_balde.get("esticado_caiu_n"))
    n_eventos = num_finito(resumo_reacao.get("n_events"))
    if esticado_n is not None and caiu_n is not None:
        legitimos = {(int(caiu_n), int(esticado_n))}
        if n_eventos is not None:
            legitimos.add((int(esticado_n), int(n_eventos)))
        for frase in frases(prosa_sa):
            if not re.search(r"esticad", frase):
                continue
            achado_par = next(
                ((int(m.group(1)), int(m.group(2)))
                 for m in _PAR_CONTADO.finditer(frase)
                 if (int(m.group(1)), int(m.group(2))) not in legitimos),
                None)
            if achado_par is None:
                continue
            add("ERRO", "ANALISE_BUCKET_CONTADO_ERRADO",
                f"conta {achado_par[0]} de {achado_par[1]} para o balde "
                f"'chegou esticado', mas o dado diz {int(caiu_n)} de "
                f"{int(esticado_n)} esticados caíram"
                + (f" (de {int(n_eventos)} eventos no total)"
                   if n_eventos is not None else "")
                + f". Trecho: “{frase.strip()[:120]}”.")
            break

    # ── 15. sessão do anúncio citada como reação (AMC) ──────────────────────
    #
    # Auditoria de 27/08/2026 (WOLF): "A reação do papel a este evento foi
    # de -7.53% no dia do anúncio, e -9.42% no dia seguinte." WOLF reporta
    # AMC -- `janela_reacao` do evento é "seguinte", ou seja, o -7,53% de
    # 19/08 é a sessão ANTERIOR à divulgação, não a reação. A reação de
    # verdade é o -9,42% de 20/08, que a própria tabela marca com ◂. O
    # texto tratou os dois números como se fossem a mesma coisa.
    #
    # O payload já carrega `janela_reacao` por evento -- a checagem não
    # infere nada, só confere se o texto contradiz o que o próprio dado diz.
    # Só olha o evento MAIS RECENTE: é dele que a seção "Earnings e
    # volatilidade" fala ("o balanço mais recente, ocorrido há N pregões").
    eventos_reacao = caminho(dados, "reaction").get("events")
    if isinstance(eventos_reacao, list) and eventos_reacao:
        evento_recente = eventos_reacao[0]
        if (isinstance(evento_recente, dict)
                and evento_recente.get("janela_reacao") == "seguinte"):
            errado = num_finito(
                dic(evento_recente.get("announcement_day")).get("close_pct"))
            if errado is not None:
                for frase in frases(prosa_sa):
                    m = _REACAO_DIA_DO_ANUNCIO.search(frase)
                    if not m:
                        continue
                    citado = float(m.group(1).replace(",", "."))
                    if abs(citado - errado) > 0.05:
                        continue  # outro número -- não é o caso coberto aqui
                    add("ERRO", "ANALISE_REACAO_SESSAO_ERRADA",
                        f"chama {errado:+.2f}% do dia do anúncio de "
                        f"'reação', mas este papel reporta depois do "
                        f"fechamento (AMC) -- a sessão que precifica o "
                        f"resultado é a SEGUINTE, não a do anúncio. Aqui o "
                        f"dia do anúncio é a sessão ANTERIOR à divulgação. "
                        f"Trecho: “{frase.strip()[:120]}”.")
                    break

    # ── 16. amostra curta de earnings sem declarar o N ──────────────────────
    #
    # Mesma regra que a tela Reação a Earnings já aplica
    # (LEITURA_AMOSTRA_CURTA_OMITIDA) -- aqui não existia, e o WOLF de
    # 27/08/2026 apresentou R1/R2/S1/S2 e médias de reação com N=4 SEM
    # nenhuma menção ao tamanho da amostra em nenhuma frase da seção.
    #
    # AVISO, não ERRO: é omissão de contexto, não contradição de dado --
    # o número em si pode estar certo.
    n_ev = num_finito(resumo_reacao.get("n_events"))
    if n_ev is not None and n_ev < N_EVENTOS_DECLARAR_ANALISE:
        frases_lista = list(frases(prosa_sa))
        # Guarda equivalente ao `if not trechos: continue` da tela Reação a
        # Earnings: se a seção nem cita a estatística de reação, não há o que
        # declarar -- "Sem menção a earnings nesta seção." não pode cair aqui.
        cita_estatistica = any(
            re.search(r"\breacao\b", f) and re.search(r"\d", f)
            for f in frases_lista)
        declarou = cita_estatistica and any(
            (cita_numero(f, n_ev, inteiro_ok=True)
             and re.search(r"\b(?:evento|observa|ocasi|caso|amostra|"
                           r"result|balan[cç]o|trimestre)", f))
            or (n_ev == 1 and re.search(
                r"\b(?:um\s+[úu]nico|[úu]nico|uma\s+[úu]nica|apenas\s+um)\b", f))
            for f in frases_lista)
        if cita_estatistica and not declarou:
            add("AVISO", "ANALISE_AMOSTRA_CURTA_OMITIDA",
                f"apresenta estatística de reação a earnings sem declarar "
                f"que a amostra é de {n_ev} evento(s) — abaixo de "
                f"{N_EVENTOS_DECLARAR_ANALISE}, o SYSTEM pede o número "
                f"citado.")

    # ── 17. número pré-reação comparado à média histórica da reação ─────────
    #
    # Auditoria de 27/08/2026 (MRVL): "o padrão esticado teve reação média
    # de -1,23%, próxima ao -1,49% observado agora." O -1,49% é o
    # fechamento do DIA DO ANÚNCIO -- MRVL reporta AMC, a reação de verdade
    # (D+1) ainda não aconteceu (a própria tabela mostra "— ◂" nesse
    # evento). A frase compara um número PRÉ-reação com a média histórica
    # de reações JÁ CONFIRMADAS como se fossem a mesma fase do preço --
    # mesmo o texto dizendo, duas frases antes, que "falta o pregão que
    # historicamente carrega a maior parte da reação".
    #
    # Só dispara quando TRÊS coisas colidem na MESMA frase: o número da
    # média do balde (esticado/descontado), o número pré-reação, e uma
    # palavra que os conecta como equivalentes -- três condições
    # independentes, o risco de coincidência é baixo.
    if isinstance(eventos_reacao, list) and eventos_reacao:
        evento_pre_reacao = eventos_reacao[0]
        if (isinstance(evento_pre_reacao, dict)
                and evento_pre_reacao.get("janela_reacao") == "seguinte"
                and dic(evento_pre_reacao.get("next_day")).get("close_pct") is None):
            pre_reacao = num_finito(
                dic(evento_pre_reacao.get("announcement_day")).get("close_pct"))
            estado_atual = ru_balde.get("estado_atual")
            media_balde = num_finito(
                ru_balde.get("esticado_reacao_media") if estado_atual == "esticado"
                else ru_balde.get("descontado_reacao_media") if estado_atual == "descontado"
                else None)
            if pre_reacao is not None and media_balde is not None:
                for frase in frases(prosa_sa):
                    if not (cita_numero(frase, media_balde)
                            and cita_numero(frase, pre_reacao)
                            and re.search(_COMPARA_AO_ATUAL, frase)):
                        continue
                    if afirmacao_negada(frase, _COMPARA_AO_ATUAL):
                        continue
                    add("ERRO", "ANALISE_PRE_REACAO_COMO_REACAO_HISTORICA",
                        f"compara {pre_reacao:+.2f}% (fechamento do DIA DO "
                        f"ANÚNCIO, pré-reação -- este papel reporta AMC e a "
                        f"reação de verdade, D+1, ainda não aconteceu) com "
                        f"a média histórica de reações CONFIRMADAS "
                        f"({media_balde:+.2f}%), como se fossem a mesma "
                        f"fase do preço. "
                        f"Trecho: “{frase.strip()[:120]}”.")
                    break

    # ── 18. reação média atribuída ao "dia do anúncio" (AMC) ────────────────
    #
    # MRVL, 27/08/2026: "a reação média no dia do anúncio foi de +1.22% no
    # fechamento, com um gap médio de abertura de -0.29%." Os dois números
    # batem com `close_pct_mean`/`gap_pct_mean` -- mas para um papel AMC
    # essas médias vêm da sessão SEGUINTE (a própria tabela marca com ◂),
    # não do dia do anúncio. Diferente do check 15 (que pega um número
    # ISOLADO chamado de reação): aqui a ESTATÍSTICA AGREGADA inteira leva
    # o rótulo errado -- o número costuma estar certo, o QUANDO é que não
    # está.
    if isinstance(eventos_reacao, list) and eventos_reacao:
        evento_amc = eventos_reacao[0]
        if isinstance(evento_amc, dict) and evento_amc.get("janela_reacao") == "seguinte":
            for frase in frases(prosa_sa):
                if not re.search(_REACAO_MEDIA_E_ANUNCIO, frase):
                    continue
                if afirmacao_negada(frase, _REACAO_MEDIA_E_ANUNCIO):
                    continue
                add("ERRO", "ANALISE_REACAO_MEDIA_ATRIBUIDA_AO_ANUNCIO",
                    f"chama a estatística de reação média de 'no dia do "
                    f"anúncio', mas este papel reporta depois do "
                    f"fechamento (AMC) -- a sessão que precifica o "
                    f"resultado é a SEGUINTE, e é dela que vêm as médias "
                    f"de fechamento/gap desta seção. "
                    f"Trecho: “{frase.strip()[:120]}”.")
                break

    return achados


# Folga entre o percentual escrito e o calculado. O modelo às vezes usa o
# PREÇO como base e às vezes o nível ("3,55% abaixo da MM50" contra os "-3,42%"
# do painel são a mesma distância medida de pontos diferentes), então as duas
# bases contam como acerto de magnitude.
_FOLGA_DE_MAGNITUDE = 0.06


def _bate_a_magnitude(citado: float, preco: float, nivel: float) -> bool:
    for base in (nivel, preco):
        real = abs(preco - nivel) / base * 100
        if abs(citado - real) <= max(_FOLGA_DE_MAGNITUDE, real * 0.02):
            return True
    return False


# "58,51% abaixo da maxima", "96,81% acima da minima" -- o numero, a direcao
# e a referencia anual. `do dia`/`intraday` fica de fora: maxima do dia e outro
# dado, e apontar contra o `yearHigh` seria acusar o texto de dizer o que ele
# nao disse.
_DISTANCIA_DA_FAIXA = re.compile(
    # MRVL, 27/08/2026 (2ª rodada): "36.62% DISTANTE de sua máxima" escapou
    # -- "distante" faltava na alternativa, e "sua" entre a preposição e o
    # substantivo quebrava o casamento mesmo com "distante" adicionado. O
    # ERRO era o MESMO número (36,62%, base trocada) que a 1ª rodada já
    # tinha pego com "abaixo da máxima" -- a checagem escapou só porque a
    # 2ª rodada disse a MESMA coisa com outras palavras.
    r"(\d+(?:[.,]\d+)?)\s*%\s*(?:acima|abaixo|distante)?\s*(?:d[eo]|da)\s+"
    r"(?:d[ao]s?\s+|sua\s+|pr[óo]pria\s+|sua\s+pr[óo]pria\s+)?"
    r"(m[áa]xima|m[íi]nima|topo|fundo)"
    r"(?!\s+(?:do\s+dia|di[áa]ri\w+|intrad\w+|da\s+sess[ãa]o))",
    re.IGNORECASE)


def _bases_da_distancia_trocadas(frase: str, preco: float, lo: float,
                                 hi: float) -> list[tuple]:
    """[(rotulo, citado, referencia, distancia_correta), ...].

    So entra na lista o percentual que bate com a conta feita sobre o PRECO
    e NAO bate com a conta sobre a referencia -- ver a nota da checagem 9b
    sobre por que a base importa aqui e nao nas medias moveis.

    Devolve TODOS os achados da frase, nao o primeiro: a forma tipica do erro
    e' citar maxima e minima na mesma frase, com a mesma base trocada nas
    duas. Reportar so uma deixaria a outra parecendo conferida."""
    achados: list[tuple] = []
    for m in _DISTANCIA_DA_FAIXA.finditer(frase):
        citado = num_finito(m.group(1))
        if citado is None:
            continue
        palavra = m.group(2).lower()
        de_cima = palavra.startswith("max") or palavra.startswith("máx") \
            or palavra == "topo"
        referencia = hi if de_cima else lo
        rotulo = "máxima de 52 semanas" if de_cima else "mínima de 52 semanas"
        if not referencia:
            continue
        correto = (preco - referencia) / referencia * 100
        errado = (preco - referencia) / preco * 100
        if _perto(citado, abs(correto)):
            continue                      # a conta certa: nada a apontar
        if _perto(citado, abs(errado)):
            achados.append((rotulo, citado, referencia, correto))
    return achados


# A media movel como SUJEITO da frase: "a MM20 esta' 3% abaixo da MM50".
#
# Existe para nao acusar a frase que estava CERTA. Na mesma analise de ARM que
# motivou a checagem, esta passou intacta:
#
#     "A MM200, em US$ 198,40, [...] estando 20,98% abaixo do preco atual."
#
# 198,40/251,06-1 = -20,98%. Confere -- porque ali o sujeito e' a MEDIA e a
# referencia e' o PRECO, o inverso das outras frases. Quem mede o que muda
# qual conta e' a certa, e um validador que ignora isso apontaria contra o
# unico trecho correto do paragrafo.
#
# `est[áa]` com fronteira, e nao `est\w*`: "estando" e' o gerundio da frase do
# MM200 acima, onde a media aparece antes mas NAO e' o sujeito da comparacao
# medida em porcentagem.
_MEDIA_COMO_SUJEITO = re.compile(
    r"(?:" + "|".join(escrita for _, escrita, _ in _REFERENCIAS) + r")"
    r"[^,;.]{0,40}?\best[áa]\b", re.IGNORECASE)

# Quem e' o sujeito quando NAO e' uma media.
_SUJEITO_E_O_PRECO = re.compile(
    r"\b(?:pre[çc]o|papel|ativo|a[çc][ãa]o|cota[çc][ãa]o|ticker)\b",
    re.IGNORECASE)


def _base_da_media_trocada(frase: str, preco: float, dados) -> list[tuple]:
    """[(rotulo, citado, nivel, correto), ...] -- distancia a media movel
    calculada sobre o PRECO em vez de sobre a media.

    Incidente real (ARM, 26/08/2026), publicado sem apontamento:

        "O preco atual de US$ 251,06 esta' 3,78% abaixo da MM20 (US$ 260,55)
         [...] e 17,22% abaixo da MM50 (US$ 294,28)"

    O certo e' -3,64% e -14,69%. Os dois numeros sairam de dividir pelo PRECO:
    260,55/251,06-1 = +3,78% e 294,28/251,06-1 = +17,22%.

    ## Por que agora, se a nota da 9b dizia que a base nao importa nas medias

    Ela dizia que preco e media andam perto e a convencao nao muda o numero o
    bastante para valer a briga. O argumento e' bom onde e' verdade e fraco
    exatamente onde importa: quanto MAIS longe da media, maior a divergencia.
    Na MM20 dá 0,14pp; na MM50, 2,53pp.

    E ha' um fato que aquela nota nao tinha: o painel Tecnica, na MESMA tela,
    imprime "-14,69% de distancia" para a MM50. O campo `pctAboveSma50` e'
    `_pct_diff(price, sma50)` = (preco-media)/media -- ou seja, este produto
    JA' TEM uma convencao publicada. A prosa nao esta escolhendo outra
    convencao legitima, esta contradizendo o painel dois paragrafos acima.

    ## Guardas

    Tres, e nenhuma e' opcional:

    1. o sujeito precisa ser o PRECO (ver `_MEDIA_COMO_SUJEITO`);
    2. so' aponta quando o citado bate com a conta ERRADA e nao com a certa --
       numero que nao bate com nenhuma das duas e' outra conversa, mesmo
       criterio da 9b;
    3. quando as duas contas ficam dentro da folga UMA DA OUTRA, nao da' para
       atribuir o numero a nenhuma delas. Nao e' cortesia, e' honestidade: o
       apontamento afirmaria saber de onde veio um numero que e' compativel
       com as duas origens.

    Devolve TODOS os achados da frase: a forma tipica e' citar MM20 e MM50
    juntas, com a mesma base trocada nas duas.
    """
    achados: list[tuple] = []
    for rotulo, escrita, campos in _REFERENCIAS:
        for m in re.finditer(_DIRECAO + rf"(?:{escrita})", frase):
            antes = frase[:m.start()]
            if _MEDIA_COMO_SUJEITO.search(antes):
                continue          # o sujeito e' outra media, nao o preco
            if not _SUJEITO_E_O_PRECO.search(antes):
                continue          # sujeito indeterminado: silencio
            citado = num_finito(m.group(1))
            nivel = _primeiro_valor(*(caminho(dados, secao).get(campo)
                                      for secao, campo in campos))
            if citado is None or not nivel:
                continue
            correto = (preco - nivel) / nivel * 100
            errado = (nivel - preco) / preco * 100
            if _perto(citado, abs(correto)):
                continue
            if abs(abs(correto) - abs(errado)) <= _FOLGA_DE_MAGNITUDE:
                continue
            if _perto(citado, abs(errado)):
                achados.append((rotulo, citado, nivel, correto))
    return achados


def _perto(citado: float, real: float) -> bool:
    return abs(citado - real) <= max(_FOLGA_DE_MAGNITUDE, real * 0.02)


def _direcao_contradita(frase: str, preco: float, dados) -> tuple | None:
    """(rótulo, citado, direção_dita, valor_do_nível, distância_real) ou None.

    Só devolve quando a magnitude bate E a direção não — ver a nota da
    checagem 8 sobre por que a magnitude é a guarda do sujeito da frase."""
    for rotulo, escrita, campos in _REFERENCIAS:
        for m in re.finditer(_DIRECAO + rf"(?:{escrita})", frase):
            citado = num_finito(m.group(1))
            nivel = _primeiro_valor(*(caminho(dados, secao).get(campo)
                                      for secao, campo in campos))
            if citado is None or not nivel:
                continue
            if not _bate_a_magnitude(citado, preco, nivel):
                continue
            real = preco - nivel
            dito_acima = m.group(2) == "acima"
            if dito_acima != (real > 0):
                return rotulo, citado, m.group(2), nivel, real
    return None


def _distancias_citadas(frase: str, preco) -> list:
    """[(posição, distância assinada, unidade)] de tudo que a frase cita como
    nível.

    Duas grafias: o percentual COM SINAL, que já é a distância, e o valor em
    US$, cuja distância sai do preço atual. A unidade viaja junto porque a
    mensagem do apontamento precisa dela -- "+10,33%" e "+1,98" (dólares) são
    coisas diferentes para quem lê."""
    saida = [(m.start(), num_finito(m.group(1)), "%")
             for m in re.finditer(_DISTANCIA_ASSINADA, frase)]
    if preco and re.search(_FALA_DE_NIVEL, frase):
        for m in re.finditer(_VALOR_EM_DOLAR, frase):
            v = num_finito(m.group(1))
            if v is not None:
                saida.append((m.start(), v - preco, "US$"))
    return sorted((pos, d, u) for pos, d, u in saida if d)


def _ordem_invertida(frase: str, preco=None) -> tuple | None:
    """(distância citada antes, distância citada depois) quando a segunda está
    MAIS PERTO do preço que a primeira — ou seja, a ordem está trocada.

    Compara só o par que a marca de sequência separa, e só quando os dois têm
    o MESMO sinal: "a MM20 (+10,33%), seguida abaixo pelo S1 (-9,31%)" fala de
    lados opostos e não é uma sequência de distâncias."""
    citadas = _distancias_citadas(frase, preco)
    if len(citadas) < 2:
        return None
    for marca in _SEQUENCIA:
        for m in re.finditer(marca, frase):
            antes = [(d, u) for pos, d, u in citadas if pos < m.start()]
            depois = [(d, u) for pos, d, u in citadas if pos >= m.end()]
            if not antes or not depois:
                continue
            (a, ua), (d, ud) = antes[-1], depois[0]
            if (a > 0) != (d > 0) or ua != ud:
                continue  # lados opostos, ou grafias diferentes: não comparam
            if abs(d) < abs(a):
                return a, d, ua
    return None


def _estatistica_trocada(frase: str, resumo: dict) -> tuple | None:
    """(rótulo, citado, valores esperados, campo que o número realmente é).

    Cada percentual pertence ao rótulo MAIS PRÓXIMO dele na frase. A primeira
    versão varria todos os percentuais procurando um que não batesse com o
    rótulo encontrado -- e numa frase com DUAS estatísticas isso atribuía o
    número de uma ao nome da outra:

      "reação média absoluta de 15,09% no fechamento e um gap médio absoluto
       de 12,16% na abertura"

    Os dois números estavam certos (SMCI, 26/08/2026), e a checagem acusou os
    15,09% de serem o gap -- ela própria cometendo o erro de rótulo trocado
    que existe para pegar.

    Só olha frases que dizem MÉDIA: "o gap de +2,19% em abril" cita um evento
    específico e não é o campo do resumo."""
    if not re.search(r"m[ée]di[ao]", frase):
        return None

    # UMA ORAÇÃO, UM RÓTULO, UM NÚMERO. Nem distância nem "olhar para trás"
    # resolvem os dois sentidos do português: em "12,16% na abertura E reação
    # média de 15,09% no fechamento", o "abertura" do primeiro trecho fica
    # antes do segundo número e o roubaria. A fronteira de oração é o que
    # separa de verdade.
    # A vírgula separa oração SÓ quando não está entre dígitos: em pt-BR ela é
    # o separador decimal, e dividir sem essa guarda parte "15,09" em "15" e
    # "09%" -- um número inventado no meio da checagem que existe para pegar
    # número trocado.
    for oracao in re.split(r"\s+(?:e|mas|enquanto)\s+|;|(?<!\d),(?!\d)", frase):
        marcas = [(m.start(), rotulo, campos)
                  for rotulo, escrita, campos in _ESTATISTICAS
                  for m in re.finditer(escrita, oracao)]
        if not marcas:
            continue
        for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*%", oracao):
            citado = num_finito(m.group(1))
            if citado is None:
                continue
            _, rotulo, campos = min(marcas, key=lambda x: abs(x[0] - m.start()))
            esperados = [v for v in (num_finito(resumo.get(c)) for c in campos)
                         if v is not None]
            if not esperados:
                continue
            if any(abs(citado - abs(v)) <= 0.06 for v in esperados):
                continue  # bate com o campo do rótulo desta oração
            bate_com = next(
                (c for c in _CAMPOS_DE_RESUMO
                 if (v := num_finito(resumo.get(c))) is not None
                 and abs(citado - abs(v)) <= 0.06), None)
            return rotulo, citado, esperados, bate_com
    return None


def _cita_percentual(frase: str, valor: float) -> bool:
    """O número aparece COMO PERCENTUAL, não como qualquer algarismo solto.

    "a reação foi medida 14,92 horas depois" citava 14,92 e caía; exigir o `%`
    colado separa o dado da coincidência."""
    for g in grafias(valor, inteiro_ok=False):
        if re.search(rf"(?<![\d.,]){re.escape(g)}\s*%", frase):
            return True
    return False


def bloco_de_correcao(achados: list) -> str:
    return _bloco(achados, "mantendo as seções e o tamanho pedidos")
