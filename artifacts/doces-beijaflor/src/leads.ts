import fs from "node:fs";
import path from "node:path";

export interface Lead {
  id: number;
  name: string;
  whatsapp: string; // só dígitos, com DDI 55 na frente
  source: string; // de onde veio o cadastro (ex: "ifood-qr")
  createdAt: string;
}

// Persistência em arquivo JSON -- deliberadamente simples pra fase 1 (captura
// de leads via QR Code): sem banco pra configurar, o arquivo é o backup. Se a
// loja crescer pra pedidos online, a troca por Postgres acontece só aqui.
export class LeadStore {
  private file: string;
  private leads: Lead[];

  constructor(file: string) {
    this.file = file;
    this.leads = [];
    try {
      const raw = fs.readFileSync(file, "utf8");
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) this.leads = parsed;
    } catch {
      // arquivo ainda não existe ou está corrompido -- começa vazio.
    }
  }

  private persist(): void {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(this.leads, null, 2));
    fs.renameSync(tmp, this.file);
  }

  list(): Lead[] {
    return [...this.leads];
  }

  // Cadastra ou atualiza (dedup por WhatsApp): o mesmo cliente escaneando o QR
  // de novo não vira lead duplicado, só atualiza o nome se mudou.
  upsert(name: string, whatsapp: string, source: string): { lead: Lead; created: boolean } {
    const existing = this.leads.find((l) => l.whatsapp === whatsapp);
    if (existing) {
      existing.name = name;
      this.persist();
      return { lead: existing, created: false };
    }
    const lead: Lead = {
      id: this.leads.length ? Math.max(...this.leads.map((l) => l.id)) + 1 : 1,
      name,
      whatsapp,
      source,
      createdAt: new Date().toISOString(),
    };
    this.leads.push(lead);
    this.persist();
    return { lead, created: true };
  }
}

// Aceita formatos comuns digitados por cliente ("(11) 98765-4321", "11987654321",
// "+55 11 98765-4321") e normaliza pra dígitos com DDI 55. Retorna null se não
// parecer um celular/fixo brasileiro válido.
export function normalizeWhatsapp(input: string): string | null {
  let digits = input.replace(/\D/g, "");
  if (digits.startsWith("55") && digits.length >= 12) digits = digits.slice(2);
  // DDD (2) + número (8 fixo ou 9 celular)
  if (digits.length !== 10 && digits.length !== 11) return null;
  const ddd = parseInt(digits.slice(0, 2), 10);
  if (ddd < 11 || ddd > 99) return null;
  return `55${digits}`;
}
