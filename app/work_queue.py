"""Personal and team work queue for the authorized case workflows.

User assignments are based on names/emails present in legacy case fields.
They are discovery aids, not record-level authorization rules.
"""
from sqlalchemy import and_, exists, func, or_, select
from .models import Case, Subtask, CaseAssignment
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


def assignment_condition(user, workflows):
    """Solo asignaciones normalizadas, nunca coincidencias difusas en bandeja."""
    return exists(select(CaseAssignment.id).where(
        CaseAssignment.case_id == Case.id_caso,
        CaseAssignment.user_id == user.id,
        CaseAssignment.workflow.in_(workflows),
    ))


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
        query = query.where(assignment_condition(user, [workflow] if workflow else allowed))
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
