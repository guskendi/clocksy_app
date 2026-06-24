import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from itsdangerous import URLSafeTimedSerializer
from models import UserConfig

JUSTIFICATION_LABELS = {
    'holiday':              'Feriado',
    'bridge':               'Emenda de feriado',
    'overtime_use':         'Usar horas extras',
    'sick_day':             'Não estava bem',
    'other_absence':        'Outro motivo',
    'early_exit_overtime':  'Saída antecipada — usar horas extras',
    'early_exit_sick':      'Saída antecipada — não estava bem',
    'early_exit_unexpected':'Saída antecipada — imprevisto',
    'early_exit_other':     'Saída antecipada — outro motivo',
}

ABSENCE_TYPES = ['holiday','bridge','overtime_use','sick_day','other_absence']
EARLY_EXIT_TYPES = ['early_exit_overtime','early_exit_sick','early_exit_unexpected','early_exit_other']

DAY_NAMES = ['Domingo','Segunda','Terça','Quarta','Quinta','Sexta','Sábado']

def get_user_config(user):
    cfg = user.config
    if not cfg:
        return {
            'weekly_hours': 44.0,
            'lunch_minutes': 60,
            'work_days': [1,2,3,4,5],
            'day_schedules': {}
        }
    work_days = [int(x) for x in cfg.work_days.split(',') if x.strip()]
    try:
        day_schedules = json.loads(cfg.day_schedules or '{}')
    except Exception:
        day_schedules = {}
    return {
        'weekly_hours': cfg.weekly_hours,
        'lunch_minutes': cfg.lunch_minutes,
        'work_days': work_days,
        'day_schedules': day_schedules
    }

def time_to_minutes(t):
    if not t:
        return None
    try:
        h, m = t.split(':')
        return int(h) * 60 + int(m)
    except Exception:
        return None

def minutes_to_hhmm(mins):
    if mins is None:
        return None
    sign = '-' if mins < 0 else ''
    mins = abs(int(mins))
    h = mins // 60
    m = mins % 60
    return f'{sign}{h}h{str(m).padStart(2,"0")}' if False else f'{sign}{h}h{m:02d}'

def get_day_target_minutes(weekday, cfg):
    """Retorna a meta de minutos para um dado dia da semana (0=dom..6=sab)."""
    schedules = cfg['day_schedules']
    key = str(weekday)
    if key in schedules and schedules[key].get('entry') and schedules[key].get('exit'):
        entry_m = time_to_minutes(schedules[key]['entry'])
        exit_m = time_to_minutes(schedules[key]['exit'])
        if entry_m is not None and exit_m is not None and exit_m > entry_m:
            return (exit_m - entry_m) - cfg['lunch_minutes']
    # fallback: distribui horas semanais igualmente pelos dias de trabalho
    work_days = cfg['work_days']
    if not work_days:
        return 480
    daily = (cfg['weekly_hours'] * 60) / len(work_days)
    return daily

def get_day_default_schedule(weekday, cfg):
    """Retorna entry/exit padrão para o dia."""
    schedules = cfg['day_schedules']
    key = str(weekday)
    if key in schedules:
        return schedules[key].get('entry','08:00'), schedules[key].get('exit','18:00')
    return '08:00', '18:00'

def calc_worked_minutes(entry, exit_t, lunch_minutes, lunch_out=None, lunch_in=None):
    """
    Calcula minutos trabalhados.
    Se lunch_out e lunch_in fornecidos: (saída_almoço - entrada) + (saída - retorno_almoço)
    Caso contrário: (saída - entrada) - lunch_minutes
    """
    e = time_to_minutes(entry)
    s = time_to_minutes(exit_t)
    if e is None or s is None or s <= e:
        return None

    lo = time_to_minutes(lunch_out)
    li = time_to_minutes(lunch_in)

    if lo is not None and li is not None and li > lo:
        # Período manhã + período tarde
        morning = lo - e
        afternoon = s - li
        if morning < 0 or afternoon < 0:
            return None
        return morning + afternoon
    else:
        # Fallback: desconta intervalo fixo
        return (s - e) - lunch_minutes

def compute_month_summary(user, year, month, records_map, cfg, up_to_today=False, today=None):
    """
    Calcula resumo do mês.
    records_map: dict {date: DayRecord}
    up_to_today: se True, conta target só até hoje (para mês atual)
    Retorna dict com totais.
    """
    import calendar
    work_days = cfg['work_days']
    _, days_in_month = calendar.monthrange(year, month)

    total_target_mins = 0
    total_worked_mins = 0
    days_logged = 0
    days_pending = 0
    overtime_used_mins = 0

    if today is None:
        today = datetime.today().date()

    for d in range(1, days_in_month + 1):
        day_date = datetime(year, month, d).date()

        # Se mês atual, só conta dias até hoje para o target
        if up_to_today and day_date > today:
            continue

        weekday = day_date.weekday()  # 0=seg..6=dom
        # converter para formato 0=dom..6=sab
        wd_sun = (weekday + 1) % 7

        if wd_sun not in work_days:
            continue

        rec = records_map.get(day_date)
        jtype = rec.justification_type if rec else None

        if jtype in ('holiday', 'bridge'):
            continue  # feriado e emenda: folga total, não conta target nem worked

        if jtype in ('overtime_use', 'sick_day', 'other_absence'):
            # ausência total: conta target, não conta horas trabalhadas
            target = get_day_target_minutes(wd_sun, cfg)
            total_target_mins += target
            if jtype == 'overtime_use':
                overtime_used_mins += target
            continue

        target = get_day_target_minutes(wd_sun, cfg)
        total_target_mins += target

        if rec and rec.entry_time and rec.exit_time:
            worked = calc_worked_minutes(rec.entry_time, rec.exit_time, cfg['lunch_minutes'],
                                         getattr(rec, 'lunch_out_time', None),
                                         getattr(rec, 'lunch_in_time', None))
            if worked is not None:
                total_worked_mins += worked
                days_logged += 1
        elif day_date <= today:
            days_pending += 1

    balance = total_worked_mins - total_target_mins - overtime_used_mins

    return {
        'total_target_mins': round(total_target_mins),
        'total_worked_mins': round(total_worked_mins),
        'balance_mins': round(balance),
        'days_logged': days_logged,
        'days_pending': days_pending,
        'overtime_used_mins': round(overtime_used_mins),
    }

def generate_reset_token(email, secret_key):
    s = URLSafeTimedSerializer(secret_key)
    return s.dumps(email, salt='password-reset')

def verify_reset_token(token, secret_key, max_age=3600):
    s = URLSafeTimedSerializer(secret_key)
    try:
        email = s.loads(token, salt='password-reset', max_age=max_age)
        return email
    except Exception:
        return None

def send_reset_email(to_email, reset_url):
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')

    if not gmail_user or not gmail_password:
        print("GMAIL_USER ou GMAIL_APP_PASSWORD não configurados")
        return False

    html_body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
      <h2 style="color:#1a1a2e;margin-bottom:8px">Clocksy</h2>
      <p style="color:#555;margin-bottom:24px">Você solicitou a redefinição de senha.</p>
      <a href="{reset_url}" style="display:inline-block;background:#1a1a2e;color:#fff;
         padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:500">
        Redefinir minha senha
      </a>
      <p style="color:#999;font-size:13px;margin-top:24px">
        Este link expira em 1 hora. Se você não solicitou isso, ignore este e-mail.
      </p>
    </div>"""

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Redefinição de senha — Clocksy'
        msg['From'] = f'Clocksy <{gmail_user}>'
        msg['To'] = to_email
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        return False
