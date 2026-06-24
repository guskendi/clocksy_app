from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date
import bcrypt

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reset_token = db.Column(db.String(255), nullable=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)
    must_change_password = db.Column(db.Boolean, default=False)

    config = db.relationship('UserConfig', backref='user', uselist=False, cascade='all, delete-orphan')
    records = db.relationship('DayRecord', backref='user', cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password):
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())


class UserConfig(db.Model):
    __tablename__ = 'user_configs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    weekly_hours = db.Column(db.Float, default=44.0)
    lunch_minutes = db.Column(db.Integer, default=60)
    # dias de trabalho: JSON string ex: "1,2,3,4,5" (0=dom,1=seg,...,6=sab)
    work_days = db.Column(db.String(20), default='1,2,3,4,5')
    # horários por dia: JSON string ex: {"1":"08:00-18:00","2":"08:00-18:00",...}
    day_schedules = db.Column(db.Text, default='{}')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DayRecord(db.Model):
    __tablename__ = 'day_records'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    record_date = db.Column(db.Date, nullable=False)
    entry_time = db.Column(db.String(5), nullable=True)        # HH:MM
    lunch_out_time = db.Column(db.String(5), nullable=True)   # HH:MM saída almoço
    lunch_in_time = db.Column(db.String(5), nullable=True)    # HH:MM retorno almoço
    exit_time = db.Column(db.String(5), nullable=True)        # HH:MM
    confirmed = db.Column(db.Boolean, default=False)
    # justificativa de ausência ou saída antecipada
    justification_type = db.Column(db.String(50), nullable=True)
    # tipos: holiday, bridge, sick, overtime_use, early_exit_overtime,
    #        early_exit_sick, early_exit_unexpected, other
    justification_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'record_date', name='uq_user_date'),)
