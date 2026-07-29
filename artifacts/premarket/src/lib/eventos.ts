// Agenda de eventos macro/setoriais usada no Painel de Cenários
// (pages/cenarios.tsx). Extraído pra módulo compartilhado pra poder ser
// reaproveitado nos marcadores de evento do gráfico de candles no futuro
// (ver "Próximos passos" na tarefa do Painel de Cenários) -- ainda não
// consumido lá, a camada de marcadores hoje só existe pra notícias
// (components/news-markers.tsx), não pra agenda macro/earnings.
export interface EventoAgenda {
  d: string; // dd/mm
  txt: string;
  peso: 1 | 2 | 3; // 3 = data do saque/alvo, 2 = evento de alto impacto, 1 = baixo impacto
}

export const AGENDA: EventoAgenda[] = [
  { d: "30/07", txt: "PCE + PIB + Amazon", peso: 1 },
  { d: "04/08", txt: "SMCI · resultado", peso: 2 },
  { d: "26/08", txt: "NVDA · resultado", peso: 2 },
  { d: "27/08", txt: "MRVL · resultado", peso: 1 },
  { d: "02/09", txt: "AVGO · resultado", peso: 1 },
  { d: "17/09", txt: "FOMC · risco de alta", peso: 2 },
  { d: "07/10", txt: "Saque", peso: 3 },
];
