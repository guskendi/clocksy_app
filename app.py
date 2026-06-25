import os
import json
import calendar
from datetime import datetime, date, timedelta
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate
from dotenv import load_dotenv
from models import db, User, UserConfig, DayRecord
from utils import (
    get_user_config, calc_worked_minutes, compute_month_summary,
    get_day_target_minutes, get_day_default_schedule,
    generate_reset_token, verify_reset_token, send_reset_email,
    JUSTIFICATION_LABELS, ABSENCE_TYPES, EARLY_EXIT_TYPES, DAY_NAMES,
    minutes_to_hhmm
)

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

_db_url = os.environ.get('DATABASE_URL', 'sqlite:///clocksy.db')
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,      # testa a conexão antes de usar
    'pool_recycle': 300,        # descarta conexões após 5 minutos
    'pool_timeout': 20,
    'pool_size': 5,
    'max_overflow': 2,
}

db.init_app(app)
migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para continuar.'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ─── AUTH ────────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET','POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('registro'))
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        user = User.query.filter_by(email=email).first()
        if user and user.is_active and user.check_password(password):
            login_user(user, remember=True)
            if user.is_admin:
                return redirect(url_for('admin_dashboard'))
            if user.must_change_password:
                return redirect(url_for('change_password'))
            # Verifica se precisa mostrar popup de ponto
            from datetime import date as date_cls
            _today = date_cls.today()
            _cfg = get_user_config(user)
            _wd_sun = (_today.weekday() + 1) % 7
            _show_popup = False
            if _wd_sun in _cfg['work_days']:
                _rec = DayRecord.query.filter_by(
                    user_id=user.id, record_date=_today
                ).first()
                if not _rec or (not _rec.entry_time and not _rec.justification_type):
                    _show_popup = True
            return redirect(url_for('registro', popup='1' if _show_popup else '0'))
        flash('E-mail ou senha incorretos.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET','POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_reset_token(email, app.config['SECRET_KEY'])
            reset_url = url_for('reset_password', token=token, _external=True)
            send_reset_email(email, reset_url)
        flash('Se este e-mail estiver cadastrado, você receberá as instruções em breve.', 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET','POST'])
def reset_password(token):
    email = verify_reset_token(token, app.config['SECRET_KEY'])
    if not email:
        flash('Link inválido ou expirado.', 'error')
        return redirect(url_for('forgot_password'))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('Usuário não encontrado.', 'error')
        return redirect(url_for('login'))
    if request.method == 'POST':
        pw = request.form.get('password','')
        pw2 = request.form.get('password2','')
        if len(pw) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'error')
        elif pw != pw2:
            flash('As senhas não coincidem.', 'error')
        else:
            user.set_password(pw)
            db.session.commit()
            flash('Senha redefinida com sucesso!', 'success')
            return redirect(url_for('login'))
    return render_template('reset_password.html', token=token)


# ─── TROCA DE SENHA OBRIGATÓRIA ──────────────────────────────────────────────

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if not current_user.must_change_password:
        return redirect(url_for('registro'))
    if request.method == 'POST':
        pw = request.form.get('password', '')
        pw2 = request.form.get('password2', '')
        if len(pw) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'error')
        elif pw != pw2:
            flash('As senhas não coincidem.', 'error')
        elif pw == '12345':
            flash('Escolha uma senha diferente da senha padrão.', 'error')
        else:
            current_user.set_password(pw)
            current_user.must_change_password = False
            db.session.commit()
            flash('Senha alterada com sucesso!', 'success')
            return redirect(url_for('registro'))
    return render_template('change_password.html')


# ─── USUÁRIO — REGISTRO ───────────────────────────────────────────────────────

@app.route('/registro')
@login_required
def registro():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    today = date.today()
    year = int(request.args.get('year', today.year))
    month = int(request.args.get('month', today.month))
    cfg = get_user_config(current_user)
    work_days = cfg['work_days']

    _, days_in_month = calendar.monthrange(year, month)
    records = DayRecord.query.filter_by(user_id=current_user.id).filter(
        DayRecord.record_date >= date(year, month, 1),
        DayRecord.record_date <= date(year, month, days_in_month)
    ).all()
    records_map = {r.record_date: r for r in records}

    days = []
    for d in range(1, days_in_month + 1):
        day_date = date(year, month, d)
        weekday = day_date.weekday()
        wd_sun = (weekday + 1) % 7
        if wd_sun not in work_days:
            continue
        rec = records_map.get(day_date)
        default_entry, default_exit = get_day_default_schedule(wd_sun, cfg)
        target_mins = get_day_target_minutes(wd_sun, cfg)
        jtype = rec.justification_type if rec else None

        worked_mins = None
        balance_mins = None
        if rec and rec.entry_time and rec.exit_time and jtype not in ABSENCE_TYPES[:4]:
            worked_mins = calc_worked_minutes(rec.entry_time, rec.exit_time, cfg['lunch_minutes'],
                                              getattr(rec, 'lunch_out_time', None),
                                              getattr(rec, 'lunch_in_time', None))
            if worked_mins is not None:
                balance_mins = worked_mins - target_mins

        days.append({
            'date': day_date,
            'day': d,
            'weekday': wd_sun,
            'weekday_name': DAY_NAMES[wd_sun],
            'is_today': day_date == today,
            'record': rec,
            'default_entry': default_entry,
            'default_exit': default_exit,
            'target_mins': target_mins,
            'worked_mins': worked_mins,
            'balance_mins': balance_mins,
            'jtype': jtype,
            'jlabel': JUSTIFICATION_LABELS.get(jtype,'') if jtype else '',
            'confirmed': rec.confirmed if rec else False,
        })

    is_current_month = (year == today.year and month == today.month)
    summary = compute_month_summary(current_user, year, month, records_map, cfg,
                                    up_to_today=is_current_month, today=today)
    months_list = [(y, m) for y in range(today.year-1, today.year+2) for m in range(1,13)]
    month_names = ['','Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                   'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']

    return render_template('registro.html',
        days=days, year=year, month=month,
        month_name=month_names[month],
        summary=summary,
        today=today,
        is_current_month=is_current_month,
        minutes_to_hhmm=minutes_to_hhmm,
        ABSENCE_TYPES=ABSENCE_TYPES,
        EARLY_EXIT_TYPES=EARLY_EXIT_TYPES,
        JUSTIFICATION_LABELS=JUSTIFICATION_LABELS,
    )


# ─── API — totais do mês (para atualização dinâmica) ─────────────────────────

@app.route('/api/summary')
@login_required
def api_summary():
    today = date.today()
    year = int(request.args.get('year', today.year))
    month = int(request.args.get('month', today.month))
    cfg = get_user_config(current_user)
    _, days_in_month = calendar.monthrange(year, month)
    records = DayRecord.query.filter_by(user_id=current_user.id).filter(
        DayRecord.record_date >= date(year, month, 1),
        DayRecord.record_date <= date(year, month, days_in_month)
    ).all()
    records_map = {r.record_date: r for r in records}

    # Para o mês atual, calcula saldo só até hoje
    is_current_month = (year == today.year and month == today.month)
    summary = compute_month_summary(current_user, year, month, records_map, cfg,
                                    up_to_today=is_current_month, today=today)

    overtime_mins = max(0, summary['balance_mins'])
    return jsonify({
        'worked': minutes_to_hhmm(summary['total_worked_mins']),
        'target': minutes_to_hhmm(summary['total_target_mins']),
        'balance': minutes_to_hhmm(summary['balance_mins']),
        'balance_mins': summary['balance_mins'],
        'overtime': minutes_to_hhmm(overtime_mins),
        'overtime_mins': overtime_mins,
        'days_logged': summary['days_logged'],
        'days_pending': summary['days_pending'],
        'is_current_month': is_current_month,
    })


# ─── API — salvar registro do dia ────────────────────────────────────────────

@app.route('/api/record', methods=['POST'])
@login_required
def save_record():
    data = request.get_json()
    record_date = datetime.strptime(data['date'], '%Y-%m-%d').date()

    rec = DayRecord.query.filter_by(user_id=current_user.id, record_date=record_date).first()
    if not rec:
        rec = DayRecord(user_id=current_user.id, record_date=record_date)
        db.session.add(rec)

    if 'entry_time' in data:
        rec.entry_time = data['entry_time'] or None
    if 'lunch_out_time' in data:
        rec.lunch_out_time = data['lunch_out_time'] or None
    if 'lunch_in_time' in data:
        rec.lunch_in_time = data['lunch_in_time'] or None
    if 'exit_time' in data:
        rec.exit_time = data['exit_time'] or None
    if 'confirmed' in data:
        rec.confirmed = bool(data['confirmed'])
    if 'justification_type' in data:
        rec.justification_type = data['justification_type'] or None
    if 'justification_note' in data:
        rec.justification_note = data['justification_note'] or None

    rec.updated_at = datetime.utcnow()
    db.session.commit()

    cfg = get_user_config(current_user)
    weekday = record_date.weekday()
    wd_sun = (weekday + 1) % 7
    target_mins = get_day_target_minutes(wd_sun, cfg)
    if rec.justification_type == 'bridge':
        target_mins /= 2

    worked_mins = None
    balance_mins = None
    if rec.entry_time and rec.exit_time:
        worked_mins = calc_worked_minutes(rec.entry_time, rec.exit_time, cfg['lunch_minutes'],
                                          rec.lunch_out_time, rec.lunch_in_time)
        if worked_mins is not None:
            balance_mins = worked_mins - target_mins

    return jsonify({
        'ok': True,
        'worked': minutes_to_hhmm(worked_mins),
        'balance': minutes_to_hhmm(balance_mins),
        'balance_mins': balance_mins,
        'confirmed': rec.confirmed,
        'jtype': rec.justification_type,
        'jlabel': JUSTIFICATION_LABELS.get(rec.justification_type,'') if rec.justification_type else '',
    })


# ─── RESUMO ───────────────────────────────────────────────────────────────────

@app.route('/resumo')
@login_required
def resumo():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    today = date.today()
    year = int(request.args.get('year', today.year))
    month = int(request.args.get('month', today.month))
    cfg = get_user_config(current_user)

    is_current_month = (year == today.year and month == today.month)
    _, days_in_month = calendar.monthrange(year, month)
    records = DayRecord.query.filter_by(user_id=current_user.id).filter(
        DayRecord.record_date >= date(year, month, 1),
        DayRecord.record_date <= date(year, month, days_in_month)
    ).all()
    records_map = {r.record_date: r for r in records}
    # Para o mês atual: saldo calculado só até hoje
    summary = compute_month_summary(current_user, year, month, records_map, cfg,
                                    up_to_today=is_current_month, today=today)

    # banco de horas global — meses anteriores fechados + mês atual até hoje
    all_records = DayRecord.query.filter_by(user_id=current_user.id).all()
    all_map = {r.record_date: r for r in all_records}
    earliest = min((r.record_date for r in all_records), default=today)
    bank_balance = 0
    cur = date(earliest.year, earliest.month, 1)
    end = date(today.year, today.month, 1)
    while cur <= end:
        _, dim = calendar.monthrange(cur.year, cur.month)
        m_map = {k: v for k, v in all_map.items()
                 if k.year == cur.year and k.month == cur.month}
        is_cur_month = (cur.year == today.year and cur.month == today.month)
        s = compute_month_summary(current_user, cur.year, cur.month, m_map, cfg,
                                  up_to_today=is_cur_month, today=today)
        bank_balance += s['balance_mins']
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)

    month_names = ['','Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                   'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']

    # lista de registros do mês para exibição
    day_rows = []
    work_days = cfg['work_days']
    for d in range(1, days_in_month + 1):
        day_date = date(year, month, d)
        weekday = day_date.weekday()
        wd_sun = (weekday + 1) % 7
        if wd_sun not in work_days:
            continue
        rec = records_map.get(day_date)
        if not rec:
            continue
        jtype = rec.justification_type
        target_mins = get_day_target_minutes(wd_sun, cfg)
        if jtype == 'bridge':
            target_mins /= 2
        worked_mins = None
        balance_mins = None
        if rec.entry_time and rec.exit_time and jtype not in ('holiday','overtime_use','sick_day','other_absence'):
            worked_mins = calc_worked_minutes(rec.entry_time, rec.exit_time, cfg['lunch_minutes'],
                                              getattr(rec, 'lunch_out_time', None),
                                              getattr(rec, 'lunch_in_time', None))
            if worked_mins is not None:
                balance_mins = worked_mins - target_mins
        day_rows.append({
            'date': day_date,
            'day': d,
            'weekday_name': DAY_NAMES[wd_sun],
            'entry': rec.entry_time,
            'exit': rec.exit_time,
            'worked': minutes_to_hhmm(worked_mins),
            'balance': minutes_to_hhmm(balance_mins),
            'balance_mins': balance_mins,
            'jtype': jtype,
            'jlabel': JUSTIFICATION_LABELS.get(jtype,'') if jtype else '',
            'confirmed': rec.confirmed,
        })

    return render_template('resumo.html',
        year=year, month=month,
        month_name=month_names[month],
        summary=summary,
        bank_balance_mins=bank_balance,
        day_rows=day_rows,
        minutes_to_hhmm=minutes_to_hhmm,
        month_names=month_names,
        today=today,
        is_current_month=is_current_month,
        JUSTIFICATION_LABELS=JUSTIFICATION_LABELS,
    )


# ─── CONFIG ───────────────────────────────────────────────────────────────────

@app.route('/config', methods=['GET','POST'])
@login_required
def config():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    cfg_obj = current_user.config
    if not cfg_obj:
        cfg_obj = UserConfig(user_id=current_user.id)
        db.session.add(cfg_obj)
        db.session.commit()

    if request.method == 'POST':
        weekly_hours = float(request.form.get('weekly_hours', 44))
        lunch_minutes = int(request.form.get('lunch_minutes', 60))
        work_days_raw = request.form.getlist('work_days')
        work_days_str = ','.join(work_days_raw)

        day_schedules = {}
        for wd in range(7):
            entry = request.form.get(f'entry_{wd}','').strip()
            exit_t = request.form.get(f'exit_{wd}','').strip()
            if entry and exit_t:
                day_schedules[str(wd)] = {'entry': entry, 'exit': exit_t}

        cfg_obj.weekly_hours = weekly_hours
        cfg_obj.lunch_minutes = lunch_minutes
        cfg_obj.work_days = work_days_str
        cfg_obj.day_schedules = json.dumps(day_schedules)
        db.session.commit()
        flash('Configurações salvas!', 'success')
        return redirect(url_for('config'))

    cfg = get_user_config(current_user)
    try:
        day_schedules = json.loads(cfg_obj.day_schedules or '{}')
    except Exception:
        day_schedules = {}

    return render_template('config.html',
        cfg=cfg,
        day_schedules=day_schedules,
        DAY_NAMES=DAY_NAMES,
    )


# ─── ADMIN ────────────────────────────────────────────────────────────────────

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    users = User.query.filter_by(is_admin=False).order_by(User.name).all()
    return render_template('admin/dashboard.html', users=users)

@app.route('/admin/users/new', methods=['GET','POST'])
@login_required
@admin_required
def admin_new_user():
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        if not name or not email:
            flash('Preencha nome e e-mail.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('E-mail já cadastrado.', 'error')
        else:
            u = User(name=name, email=email, is_admin=False, must_change_password=True)
            u.set_password('12345')
            db.session.add(u)
            db.session.flush()
            cfg = UserConfig(user_id=u.id)
            db.session.add(cfg)
            db.session.commit()
            flash(f'Usuário {name} criado! Senha padrão: 12345', 'success')
            return redirect(url_for('admin_dashboard'))
    return render_template('admin/user_form.html', user=None)

@app.route('/admin/users/<int:uid>/edit', methods=['GET','POST'])
@login_required
@admin_required
def admin_edit_user(uid):
    user = User.query.get_or_404(uid)
    if user.is_admin:
        abort(403)
    if request.method == 'POST':
        user.name = request.form.get('name','').strip()
        user.email = request.form.get('email','').strip().lower()
        user.is_active = 'is_active' in request.form
        pw = request.form.get('password','')
        if pw:
            user.set_password(pw)
        db.session.commit()
        flash('Usuário atualizado!', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('admin/user_form.html', user=user)

@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(uid):
    user = User.query.get_or_404(uid)
    if user.is_admin:
        abort(403)
    db.session.delete(user)
    db.session.commit()
    flash('Usuário removido.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/<int:uid>/records')
@login_required
@admin_required
def admin_user_records(uid):
    user = User.query.get_or_404(uid)
    today = date.today()
    year = int(request.args.get('year', today.year))
    month = int(request.args.get('month', today.month))
    cfg = get_user_config(user)
    _, days_in_month = calendar.monthrange(year, month)
    records = DayRecord.query.filter_by(user_id=uid).filter(
        DayRecord.record_date >= date(year, month, 1),
        DayRecord.record_date <= date(year, month, days_in_month)
    ).all()
    records_map = {r.record_date: r for r in records}
    summary = compute_month_summary(user, year, month, records_map, cfg)
    month_names = ['','Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                   'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    return render_template('admin/user_records.html',
        viewed_user=user, year=year, month=month,
        month_name=month_names[month],
        records_map=records_map,
        summary=summary,
        minutes_to_hhmm=minutes_to_hhmm,
        JUSTIFICATION_LABELS=JUSTIFICATION_LABELS,
        today=today,
        month_names=month_names,
    )

@app.route('/admin/records/<int:rid>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_record(rid):
    rec = DayRecord.query.get_or_404(rid)
    uid = rec.user_id
    db.session.delete(rec)
    db.session.commit()
    flash('Registro removido.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/fix-empty-records', methods=['POST'])
@login_required
@admin_required
def admin_fix_empty_records():
    """Remove registros sem entry_time e exit_time (registros quebrados)."""
    broken = DayRecord.query.filter(
        DayRecord.entry_time == None,
        DayRecord.exit_time == None,
        DayRecord.justification_type == None
    ).all()
    count = len(broken)
    for rec in broken:
        db.session.delete(rec)
    db.session.commit()
    flash(f'{count} registro(s) incompleto(s) removido(s). Agora preencha os dias novamente.', 'success')
    return redirect(url_for('admin_dashboard'))


# ─── SETUP INICIAL ───────────────────────────────────────────────────────────

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """Rota de setup — só funciona se não existir nenhum admin ainda."""
    with app.app_context():
        db.create_all()
        if User.query.filter_by(is_admin=True).first():
            flash('Setup já foi realizado.', 'info')
            return redirect(url_for('login'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        setup_key = request.form.get('setup_key', '')

        expected_key = os.environ.get('SETUP_KEY', '')
        if not expected_key or setup_key != expected_key:
            flash('Chave de setup incorreta.', 'error')
        elif not name or not email or not password:
            flash('Preencha todos os campos.', 'error')
        elif password != password2:
            flash('As senhas não coincidem.', 'error')
        elif len(password) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'error')
        else:
            u = User(name=name, email=email, is_admin=True)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            flash('Admin criado com sucesso! Faça login.', 'success')
            return redirect(url_for('login'))

    return render_template('setup.html')


# ─── INIT ─────────────────────────────────────────────────────────────────────

@app.cli.command('create-admin')
def create_admin():
    """Cria o usuário admin inicial."""
    import click
    email = click.prompt('E-mail do admin')
    password = click.prompt('Senha', hide_input=True)
    name = click.prompt('Nome')
    if User.query.filter_by(email=email).first():
        print('Usuário já existe.')
        return
    u = User(name=name, email=email, is_admin=True)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    print(f'Admin {name} criado!')

# Cria tabelas automaticamente se não existirem (primeiro deploy)
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)
