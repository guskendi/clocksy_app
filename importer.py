import os
import json
import base64
import anthropic
from datetime import datetime, date
from io import BytesIO

# ─── EXCEL ────────────────────────────────────────────────────────────────────

def parse_time_value(val):
    """Converte vários formatos de hora para HH:MM ou None."""
    if val is None:
        return None
    # datetime.time
    if hasattr(val, 'hour'):
        return f'{val.hour:02d}:{val.minute:02d}'
    # float (Excel armazena hora como fração do dia)
    if isinstance(val, float):
        total_minutes = round(val * 24 * 60)
        h = total_minutes // 60
        m = total_minutes % 60
        if 0 <= h < 24:
            return f'{h:02d}:{m:02d}'
    # string
    s = str(val).strip()
    # HH:MM ou H:MM
    if ':' in s:
        parts = s.split(':')
        try:
            h, m = int(parts[0]), int(parts[1])
            if 0 <= h < 24 and 0 <= m < 60:
                return f'{h:02d}:{m:02d}'
        except Exception:
            pass
    return None

def parse_date_value(val, year, month):
    """Tenta extrair o dia do mês de um valor."""
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        if hasattr(val, 'day'):
            return val.day
    try:
        return int(str(val).strip().split('/')[0].split('-')[-1])
    except Exception:
        return None

def import_from_excel(file_bytes, year, month):
    """
    Tenta extrair registros de horas de um arquivo Excel.
    Retorna lista de dicts: {day, entry, lunch_out, lunch_in, exit}
    """
    import openpyxl
    wb = openpyxl.load_workbook(BytesIO(file_bytes), data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    records = []
    seen_days = set()

    # Detecta cabeçalho
    header_row = None
    col_map = {}
    keywords = {
        'dia': ['dia', 'date', 'data'],
        'entry': ['entrada', 'entry', 'início', 'inicio', 'in'],
        'lunch_out': ['saída almoço', 'saida almoco', 'almoço', 'almoco', 'lunch out', 'saída alm'],
        'lunch_in': ['retorno', 'retorno almoço', 'volta', 'lunch in', 'retorno alm'],
        'exit': ['saída', 'saida', 'exit', 'fim', 'out', 'término', 'termino'],
    }

    for i, row in enumerate(rows):
        row_strs = [str(c).lower().strip() if c is not None else '' for c in row]
        matches = 0
        temp_map = {}
        for field, kws in keywords.items():
            for j, cell in enumerate(row_strs):
                if any(kw in cell for kw in kws):
                    temp_map[field] = j
                    matches += 1
                    break
        if matches >= 2:
            header_row = i
            col_map = temp_map
            break

    if not col_map:
        # Sem cabeçalho detectado — tenta por posição (dia, entrada, saída)
        col_map = {'dia': 0, 'entry': 1, 'exit': 2}
        header_row = 0

    start = (header_row + 1) if header_row is not None else 0

    for row in rows[start:]:
        if not any(row):
            continue
        day = None
        if 'dia' in col_map and col_map['dia'] < len(row):
            day = parse_date_value(row[col_map['dia']], year, month)
        if not day:
            continue
        if day in seen_days or not (1 <= day <= 31):
            continue
        seen_days.add(day)

        def get_time(field):
            if field in col_map and col_map[field] < len(row):
                return parse_time_value(row[col_map[field]])
            return None

        entry     = get_time('entry')
        lunch_out = get_time('lunch_out')
        lunch_in  = get_time('lunch_in')
        exit_t    = get_time('exit')

        if entry or exit_t:
            records.append({
                'day': day,
                'entry': entry,
                'lunch_out': lunch_out,
                'lunch_in': lunch_in,
                'exit': exit_t,
            })

    return sorted(records, key=lambda x: x['day'])


# ─── IMAGEM (Claude Vision) ────────────────────────────────────────────────────

def import_from_image(file_bytes, mime_type, year, month):
    """
    Usa Claude Vision para extrair registros de horas de uma imagem.
    Retorna lista de dicts: {day, entry, lunch_out, lunch_in, exit}
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY não configurada")

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(file_bytes).decode('utf-8')

    prompt = f"""Esta imagem contém um registro de ponto ou controle de horas de trabalho referente a {month:02d}/{year}.

Extraia todos os registros de dias com horários e retorne SOMENTE um JSON válido, sem texto adicional, no seguinte formato:

{{
  "records": [
    {{
      "day": 1,
      "entry": "08:00",
      "lunch_out": "12:00",
      "lunch_in": "13:00",
      "exit": "17:00"
    }}
  ]
}}

Regras:
- "day" é o número do dia do mês (inteiro)
- Todos os horários devem estar no formato HH:MM (24h)
- Se um campo não estiver visível ou não existir, use null
- Inclua apenas dias que tenham pelo menos entrada OU saída
- Ignore dias sem nenhum registro de horário
- Se houver texto de justificativa (feriado, folga, etc), ignore o dia"""

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": b64,
                    }
                },
                {"type": "text", "text": prompt}
            ]
        }]
    )

    raw = response.content[0].text.strip()
    # Remove possíveis backticks
    raw = raw.replace('```json', '').replace('```', '').strip()

    data = json.loads(raw)
    records = data.get('records', [])

    # Valida e normaliza
    clean = []
    for r in records:
        day = r.get('day')
        if not day or not isinstance(day, int) or not (1 <= day <= 31):
            continue
        clean.append({
            'day': day,
            'entry':     r.get('entry'),
            'lunch_out': r.get('lunch_out'),
            'lunch_in':  r.get('lunch_in'),
            'exit':      r.get('exit'),
        })

    return sorted(clean, key=lambda x: x['day'])
