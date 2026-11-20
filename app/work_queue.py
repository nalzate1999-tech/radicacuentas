"""Personal and team work queue for the authorized case workflows.

User assignments are based on names/emails present in legacy case fields.
They are discovery aids, not record-level authorization rules.
"""
from sqlalchemy import and_, exists, func, or_, select
from .models import Case, Subtask
from .services import normalize

ARCHIVED = ('CERRADA', 'CERRADO', 'PAGADA', 'PAGADO')
WORKFLOW_LABELS = {
    'revision': 'Revision del supervisor',
    'seguimiento': 'Gestion y seguimiento',
    'juridica': 'Juridica / tecnica',
}
WORKFLOW_PATHS = {'revision': 'revisor', 'seguimiento': 'seguimiento', 'juridica': 'abogados'}


def authorized_workflows(user_permissions):
    return [key for key in WORKFLOW_LABELS if key in user_permissions]


def workflow_condition(key):
    """Only cases currently in that workflow's actionable stage."""
    if key == 'revision':
        return or_(
            Case.current_owner.ilike('Revisor'),
            exists(select(Subtask.id).where(
                Subtask.case_id == Case.id_caso,
                Subtask.kind == 'REVISOR',
                Subtask.active.is_(True),
            )),
        )
    if key == 'juridica':
        return or_(
            Case.current_owner.ilike('Firma Juridica'),
            Case.current_owner.ilike('Firma Jur\u00eddica'),
            Case.current_owner.ilike('Firma Tecnica'),
            Case.current_owner.ilike('Firma T\u00e9cnica'),
            exists(select(Subtask.id).where(
                Subtask.case_id == Case.id_caso,
                Subtask.kind.in_(['TECNICA', 'JURIDICA']),
                Subtask.active.is_(True),
            )),
        )
    if key == 'seguimiento':
        return or_(Case.current_owner.ilike('Seguimiento'),
                   Case.current_status == 'RECIBIDA')
    raise ValueError('Tipo de gestion no reconocido.')


def assignment_condition(user):
    """Exact case-insensitive name/email; does not infer ownership from a shared role."""
    name = (user.name or '').strip().casefold()
    email = (user.email or '').strip().casefold()
    identifiers = sorted({v for v in (name, email) if v})
    if not identifiers:
        return Case.id_caso.is_(None)
    names = (Case.followup_assignee, Case.reviewer, Case.supervisor_name,
             Case.current_owner, Case.sent_by)
    cols = [func.lower(func.trim(col)).in_(identifiers) for col in names]
    for email_col in (Case.supervisor_email,):
        if email:
            cols.append(func.lower(func.trim(email_col)) == email)
    cols.append(exists(select(Subtask.id).where(
        Subtask.case_id == Case.id_caso,
        func.lower(func.trim(Subtask.responsible)).in_(identifiers),
    )))
    return or_(*cols)


def queue_query(perms, user, workflow='', scope='mios'):
    allowed = authorized_workflows(perms)
    if not allowed:
        raise PermissionError('No tiene formularios asignados.')
    if workflow and workflow not in allowed:
        raise PermissionError('No tiene permiso para ese formulario.')
    conditions = [workflow_condition(workflow)] if workflow else [workflow_condition(k) for k in allowed]
    query = select(Case).where(
        ~Case.current_status.in_(ARCHIVED),
        Case.needs_reconciliation.is_not(True),
        or_(*conditions),
    )
    if scope == 'mios':
        query = query.where(assignment_condition(user))
    elif scope != 'equipo':
        raise ValueError('Vista de bandeja no reconocida.')
    return query


def recommended_workflow(case, perms, preferred=''):
    allowed = authorized_workflows(perms)
    if case.needs_reconciliation or case.current_status in ARCHIVED:
        return None
    if preferred and preferred in allowed:
        return WORKFLOW_PATHS[preferred]
    owner = normalize(case.current_owner)
    if 'revision' in allowed and ('revisor' in owner or 'segunda revision' in owner):
        return 'revisor'
    if 'juridica' in allowed and ('juridic' in owner or 'tecnic' in owner):
        return 'abogados'
    if 'seguimiento' in allowed and owner == 'seguimiento':
        return 'seguimiento'
    if 'seguimiento' in allowed:
        return 'seguimiento'
    if 'revision' in allowed and case.requires_reviewer:
        return 'revisor'
    if 'juridica' in allowed and (case.requires_legal or case.requires_technical):
        return 'abogados'
    return None
