"""
Gera Carteira_Investimentos.xlsx a partir dos dados reais do banco de dados.
Usa DATABASE_URL do ambiente (mesmo banco da interface web).
"""
import os
import sys
import psycopg2
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import CellIsRule

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERRO: DATABASE_URL nao definida.", file=sys.stderr)
    sys.exit(1)

# ── Cores e estilos ────────────────────────────────────────────────────────────
DARK_BG   = "1E1E2E"
HEADER_BG = "2A2D3E"
GREEN_BG  = "1A3A2A"
RED_BG    = "3A1A1A"
GREEN_TXT = "00C896"
RED_TXT   = "FF5C5C"
WHITE     = "FFFFFF"
LIGHT_GRAY= "C0C0C0"
YELLOW    = "FFD700"
BLUE_TXT  = "5B9BD5"
SUBHDR_BG = "343650"
ORANGE    = "FFA500"

thin = Side(style='thin', color="404060")
med  = Side(style='medium', color="5B5B8A")
border_thin = Border(left=thin, right=thin, top=thin, bottom=thin)
border_med  = Border(left=med,  right=med,  top=med,  bottom=med)

def fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)

def center():
    return Alignment(horizontal='center', vertical='center', wrap_text=True)

def right_align():
    return Alignment(horizontal='right', vertical='center')

FMT_DATE = 'DD/MM/YYYY'
FMT_CURR = 'R$ #,##0.00;(R$ #,##0.00);"-"'
FMT_PCT  = '0.00%;(0.00%);"-"'

# ── Busca dados do banco ───────────────────────────────────────────────────────
conn = psycopg2.connect(DATABASE_URL)
cur  = conn.cursor()

cur.execute("""
    SELECT
        p.ticker,
        p.avg_cost,
        pu.purchase_date,
        pu.amount
    FROM portfolio_positions p
    JOIN portfolio_purchases pu ON pu.position_id = p.id
    ORDER BY p.ticker, pu.purchase_date
""")
rows_db = cur.fetchall()
cur.close()
conn.close()

# ── Monta workbook ─────────────────────────────────────────────────────────────
wb = Workbook()
ws = wb.active
ws.title = "Carteira"

# Título
ws.merge_cells('A1:L1')
ws['A1'] = "CONTROLE DE CARTEIRA DE INVESTIMENTOS"
ws['A1'].font      = Font(name='Arial', bold=True, color=YELLOW, size=14)
ws['A1'].fill      = fill(DARK_BG)
ws['A1'].alignment = center()
ws.row_dimensions[1].height = 32

# Cabeçalhos
headers = [
    ("A", "ATIVO",           12),
    ("B", "DATA\nCOMPRA",    13),
    ("C", "PRECO\nCOMPRA",   13),
    ("D", "TOTAL\nINVESTIDO",14),
    ("E", "DATA\nVENDA",     13),
    ("F", "PRECO\nVENDA",    13),
    ("G", "TOTAL\nVENDA",    14),
    ("H", "RESULTADO\nR$",   15),
    ("I", "RESULTADO\n%",    13),
    ("J", "STATUS",          11),
]

for col, title, width in headers:
    c = ws[f"{col}2"]
    c.value     = title
    c.font      = Font(name='Arial', bold=True, color=YELLOW, size=9)
    c.fill      = fill(HEADER_BG)
    c.alignment = center()
    c.border    = border_med
    ws.column_dimensions[col].width = width

ws.row_dimensions[2].height = 30

DATA_START = 3
MAX_ROWS   = 40  # suporta até 40 operações

# ── Linhas de dados ────────────────────────────────────────────────────────────
for i, (ticker, avg_cost, purchase_date, amount) in enumerate(rows_db, start=DATA_START):
    r = i
    row_fill = fill("252840") if r % 2 == 0 else fill("1E2035")

    ws[f'A{r}'] = ticker
    ws[f'A{r}'].font = Font(name='Arial', bold=True, color=YELLOW, size=10)

    ws[f'B{r}'] = str(purchase_date)
    ws[f'B{r}'].number_format = FMT_DATE

    ws[f'C{r}'] = float(avg_cost)
    ws[f'C{r}'].number_format = FMT_CURR

    ws[f'D{r}'] = float(amount)
    ws[f'D{r}'].number_format = FMT_CURR

    # E = Data Venda (usuário preenche)
    # F = Preço Venda (usuário preenche)

    ws[f'G{r}'] = f'=IF(AND(E{r}<>"",F{r}<>""),F{r}*(D{r}/C{r}),"")'
    ws[f'G{r}'].number_format = FMT_CURR

    ws[f'H{r}'] = f'=IF(G{r}<>"",G{r}-D{r},"")'
    ws[f'H{r}'].number_format = FMT_CURR

    ws[f'I{r}'] = f'=IF(AND(H{r}<>"",D{r}<>0),H{r}/D{r},"")'
    ws[f'I{r}'].number_format = FMT_PCT

    ws[f'J{r}'] = f'=IF(E{r}<>"","Fechada","Aberta")'

    for col in 'ABCDEFGHIJ':
        c = ws[f'{col}{r}']
        c.fill   = row_fill
        c.border = border_thin
        c.alignment = center() if col in ('A', 'B', 'E', 'J') else right_align()
        if c.font.name == 'Calibri':
            c.font = Font(name='Arial', color=WHITE, size=10)

    ws.row_dimensions[r].height = 20

# Linhas vazias para novas operações
last_data = DATA_START + len(rows_db)
for r in range(last_data, DATA_START + MAX_ROWS):
    row_fill = fill("252840") if r % 2 == 0 else fill("1E2035")
    for col in 'ABCDEFGHIJ':
        c = ws[f'{col}{r}']
        c.fill      = row_fill
        c.border    = border_thin
        c.font      = Font(name='Arial', color=WHITE, size=10)
        c.alignment = center() if col in ('A', 'B', 'E', 'J') else right_align()

    ws[f'G{r}'] = f'=IF(AND(E{r}<>"",F{r}<>""),F{r}*(D{r}/C{r}),"")'
    ws[f'G{r}'].number_format = FMT_CURR
    ws[f'H{r}'] = f'=IF(G{r}<>"",G{r}-D{r},"")'
    ws[f'H{r}'].number_format = FMT_CURR
    ws[f'I{r}'] = f'=IF(AND(H{r}<>"",D{r}<>0),H{r}/D{r},"")'
    ws[f'I{r}'].number_format = FMT_PCT
    ws[f'J{r}'] = f'=IF(E{r}<>"","Fechada","Aberta")'
    ws.row_dimensions[r].height = 20

DATA_END = DATA_START + MAX_ROWS - 1

# ── Totais Gerais ──────────────────────────────────────────────────────────────
SEC = DATA_END + 2

ws.merge_cells(f'A{SEC}:J{SEC}')
ws[f'A{SEC}'] = "TOTAIS GERAIS"
ws[f'A{SEC}'].font      = Font(name='Arial', bold=True, color=DARK_BG, size=11)
ws[f'A{SEC}'].fill      = fill(YELLOW)
ws[f'A{SEC}'].alignment = center()
ws.row_dimensions[SEC].height = 24

def total_row(r, label, formula, fmt, color):
    ws.merge_cells(f'A{r}:G{r}')
    ws[f'A{r}'] = label
    ws[f'A{r}'].font      = Font(name='Arial', bold=True, color=color, size=10)
    ws[f'A{r}'].fill      = fill(SUBHDR_BG)
    ws[f'A{r}'].alignment = right_align()
    ws[f'A{r}'].border    = border_thin
    ws[f'H{r}'] = formula
    ws[f'H{r}'].number_format = fmt
    ws[f'H{r}'].font      = Font(name='Arial', bold=True, color=color, size=11)
    ws[f'H{r}'].fill      = fill(SUBHDR_BG)
    ws[f'H{r}'].alignment = right_align()
    ws[f'H{r}'].border    = border_thin
    for col in ('I', 'J'):
        ws[f'{col}{r}'].fill   = fill(SUBHDR_BG)
        ws[f'{col}{r}'].border = border_thin
    ws.row_dimensions[r].height = 24

total_row(SEC+1, "Total Investido:",
    f'=SUMPRODUCT((C{DATA_START}:C{DATA_END}<>"")*IFERROR(D{DATA_START}:D{DATA_END},0))',
    FMT_CURR, BLUE_TXT)
total_row(SEC+2, "Total Lucro (operacoes positivas):",
    f'=SUMPRODUCT((IFERROR(H{DATA_START}:H{DATA_END},0)>0)*IFERROR(H{DATA_START}:H{DATA_END},0))',
    FMT_CURR, GREEN_TXT)
total_row(SEC+3, "Total Perda (operacoes negativas):",
    f'=SUMPRODUCT((IFERROR(H{DATA_START}:H{DATA_END},0)<0)*IFERROR(H{DATA_START}:H{DATA_END},0))',
    FMT_CURR, RED_TXT)
total_row(SEC+4, "Resultado Liquido:",
    f'=SUMPRODUCT(IFERROR(H{DATA_START}:H{DATA_END},0))',
    FMT_CURR, YELLOW)
total_row(SEC+5, "Retorno Total %:",
    f'=IFERROR(H{SEC+4}/H{SEC+1},"")',
    FMT_PCT, YELLOW)

# ── Formatação condicional ─────────────────────────────────────────────────────
green_fill = PatternFill(start_color=GREEN_BG, end_color=GREEN_BG, fill_type="solid")
red_fill   = PatternFill(start_color=RED_BG,   end_color=RED_BG,   fill_type="solid")
green_font = Font(name='Arial', bold=True, color=GREEN_TXT, size=10)
red_font   = Font(name='Arial', bold=True, color=RED_TXT,   size=10)

for rng in (f'H{DATA_START}:H{DATA_END}', f'I{DATA_START}:I{DATA_END}'):
    ws.conditional_formatting.add(rng,
        CellIsRule(operator='greaterThan', formula=['0'], fill=green_fill, font=green_font))
    ws.conditional_formatting.add(rng,
        CellIsRule(operator='lessThan',    formula=['0'], fill=red_fill,   font=red_font))

ws.freeze_panes = 'A3'
ws.sheet_properties.tabColor = "00C896"

# ── Salva ──────────────────────────────────────────────────────────────────────
out = os.path.join(os.path.expanduser("~"), "Desktop", "Carteira_Investimentos.xlsx")
wb.save(out)
print(f"Salvo: {out}")
print(f"Operacoes exportadas: {len(rows_db)}")
