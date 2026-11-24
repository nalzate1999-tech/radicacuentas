"""Asignaciones del caso: tarea activa por etapa y control permanente.

El nombre histórico y sus asignaciones de seguimiento se conservan.
Sólo las tareas de revisión/firma se mueven durante un cambio de estado.
"""
from sqlalchemy import delete, select
from .models import CaseAssignment, User
from .services import BusinessError

ACTIVE_TYPES = ('revision', 'segunda_revision', 'juridica')
FORM_WORKFLOWS = ('seguimiento', 'revision', 'segunda_revision', 'juridica')
ADMIN_OWNER = 'Administración'


def user_can_manage(user, workflow):
    if not user or not user.active:
        return False
    if user.role == 'admin':
        return True
    return workflow in (user.form_permissions or []) or (
        user.form_permissions is None and
        (user.role == workflow or
         (workflow == 'revision' and user.role == 'revisor') or
         (workflow == 'juridica' and user.role == 'abogados'))
    )


def eligible_users(db, workflow):
    return [user for user in db.scalars(select(User).where(User.active.is_(True))
                                       .order_by(User.name)).all()
            if user_can_manage(user, workflow)]


def validate_target(db, user_id, workflow):
    if not user_id:
        raise BusinessError('Seleccione la persona que recibirá la siguiente etapa.')
    user = db.get(User, int(user_id))
    if not user_can_manage(user, workflow):
        raise BusinessError('El destinatario no está activo o no tiene el formulario requerido.')
    return user


def assigned_to(db, case_id, user_id, workflow):
    if not user_id:
        return False
    return db.scalar(select(CaseAssignment.id).where(
        CaseAssignment.case_id == case_id,
        CaseAssignment.user_id == user_id,
        CaseAssignment.workflow == workflow)) is not None


def stage_for(case):
    if case.current_status in ('CERRADO', 'CERRADA', 'PAGADO', 'PAGADA'):
        return 'cerrado'
    if case.current_status == 'EN_AJUSTES_CONTRATISTA_SUPERVISOR':
        return 'administracion'
    owner = (case.current_owner or '').casefold()
    if 'administrac' in owner:
        return 'administracion'
    if case.current_status == 'ENVIADA_A_SEGUNDA_REVISION' or 'segunda revisi' in owner:
        return 'segunda_revision'
    if owner == 'revisor':
        return 'revision'
    if 'firma jur' in owner or 'firma t' in owner:
        return 'juridica'
    if owner == 'seguimiento':
        return 'seguimiento'
    return 'externo'


def assign_control(db, case, user_id):
    """Una sola persona titular de control hasta cerrar la cuenta."""
    person = validate_target(db, user_id, 'seguimiento')
    db.execute(delete(CaseAssignment).where(
        CaseAssignment.case_id == case.id_caso,
        CaseAssignment.workflow == 'seguimiento',
    ))
    db.flush()
    db.add(CaseAssignment(
        case_id=case.id_caso, user_id=person.id, workflow='seguimiento',
        source='manual', matched_text='Titular permanente de control'))
    case.followup_assignee = person.name
    db.flush()
    return person


def transfer_stage(db, case, workflow, user_id=None):
    """Mueve sólo la tarea activa. El titular de control no cambia."""
    if workflow not in (*ACTIVE_TYPES, 'seguimiento', 'administracion', 'externo', 'cerrado'):
        raise BusinessError('Etapa de destino no reconocida.')
    person = validate_target(db, user_id, workflow) if workflow in ACTIVE_TYPES else None
    db.execute(delete(CaseAssignment).where(
        CaseAssignment.case_id == case.id_caso,
        CaseAssignment.workflow.in_(ACTIVE_TYPES),
    ))
    db.flush()
    if person:
        db.add(CaseAssignment(
            case_id=case.id_caso, user_id=person.id, workflow=workflow,
            source='manual', matched_text='Responsable actual de la etapa'))
    if workflow == 'administracion':
        case.current_owner = ADMIN_OWNER
    elif workflow == 'segunda_revision':
        case.current_owner = 'Segunda revisión'
    elif workflow == 'revision':
        case.current_owner = 'Revisor'
    elif workflow == 'seguimiento':
        case.current_owner = 'Seguimiento'
    db.flush()
    return person


def stage_after_followup(action):
    return {
        'Enviado a revisor': 'revision',
        'Enviado a segunda revisión': 'segunda_revision',
        'Enviado a firma revisora jurídica': 'juridica',
        'Enviado a firma revisora técnica': 'juridica',
        'Enviado a Supervisor / Contratista': 'administracion',
        'Devuelto a mesa directiva': 'externo',
        'Enviado financiera': 'externo',
        'Respuesta de mesa directiva': 'seguimiento',
        'Devuelta por financiera': 'seguimiento',
        'Cerrar': 'cerrado',
    }[action]


def stage_after_review(outcome):
    return {
        'Requiere ajustes': 'administracion',
        'Enviado a segunda revisión': 'segunda_revision',
        'Enviado a financiera': 'externo',
    }[outcome]


def adjust_new_event_owner(db, case):
    """Conserva el estado original del evento y registra el destinatario real."""
    from .models import Event
    # SQLAlchemy puede vaciar db.new durante la transferencia de asignación.
    # Buscar el último evento escrito por el movimiento, dentro de la transacción.
    db.flush()
    event = db.scalar(select(Event).where(
        Event.case_id == case.id_caso, Event.source == 'web',
    ).order_by(Event.id.desc()).limit(1))
    if event:
        event.resulting_owner = case.current_owner


REVIEW_TYPE_LABELS = {'juridica': 'Revisor jurídico', 'tecnica': 'Revisor técnico'}


def review_types_for(user):
    # Cuentas previas conservan sus dos especialidades hasta que se editen.
    if user.role == 'admin' or user.review_types is None:
        return ['juridica', 'tecnica']
    return [key for key in REVIEW_TYPE_LABELS if key in user.review_types]


def validate_specialty(user, kind):
    if kind not in review_types_for(user):
        raise BusinessError('La persona no tiene la especialidad requerida para esta revisión.')


def specialty_for_case(case):
    return 'tecnica' if 'TECNICA' in (case.current_status or '') else 'juridica'
