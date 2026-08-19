import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Barreira de erro por rota.
 *
 * Por que existe: em 18/08/2026 a tela de Reação a Earnings ficou PRETA --
 * sem cabeçalho, sem menu, sem mensagem. A causa era um `null.toFixed()`
 * num card, mas uma exceção não capturada no render desmonta a árvore React
 * INTEIRA, então um número faltando apagou o aplicativo.
 *
 * O pior daquela falha não foi o bug: foi o silêncio. Tela preta não diz que
 * houve erro, não diz onde, e é indistinguível de "não carregou ainda". O
 * usuário só descobre abrindo o console do navegador.
 *
 * Com a barreira, o estrago para na rota: o resto do app continua navegável,
 * e a tela mostra o que aconteceu em vez de nada.
 *
 * Componente de CLASSE porque componentDidCatch não tem equivalente em hooks
 * -- é a única parte do React que ainda exige classe.
 */
interface Props {
  children: ReactNode;
  /** Nome da tela, para a mensagem e para o log. */
  rotulo?: string;
}

interface State {
  erro: Error | null;
}

export class ErroNaTela extends Component<Props, State> {
  state: State = { erro: null };

  static getDerivedStateFromError(erro: Error): State {
    return { erro };
  }

  componentDidCatch(erro: Error, info: ErrorInfo): void {
    // Console e não silêncio: quem estiver com o DevTools aberto vê o stack
    // completo, que a mensagem na tela deliberadamente não mostra.
    console.error(`[${this.props.rotulo ?? "tela"}] quebrou no render:`, erro, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.erro) return this.props.children;

    return (
      <div className="p-6">
        <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4 max-w-2xl">
          <h2 className="text-base font-semibold text-red-400 mb-1">
            Esta tela falhou ao desenhar
          </h2>
          <p className="text-sm text-muted-foreground mb-3">
            O restante do aplicativo continua funcionando — use o menu para navegar.
            Se o erro persistir, recarregue a página.
          </p>
          {/* A mensagem do erro, não o stack: identifica o problema para quem
              for reportar, sem despejar detalhe interno na tela. */}
          <code className="block text-xs text-red-300/90 bg-black/30 rounded p-2 overflow-x-auto">
            {this.state.erro.message}
          </code>
          <button
            type="button"
            onClick={() => this.setState({ erro: null })}
            className="mt-3 text-xs underline text-muted-foreground hover:text-foreground"
          >
            Tentar desenhar de novo
          </button>
        </div>
      </div>
    );
  }
}
