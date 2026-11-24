"""Personal and team work queue for the authorized case workflows.

User assignments are based on names/emails present in legacy case fields.
They are discovery aids, not record-level authorization rules.
"""
from sqlalchemy import and_, exists, func, or_, select
from .models import Case, Subtask, CaseAssignment
from .services import normalize

ARCHIVED = ('CERRADA', 'CERRADO', 'PAGADA', 'PAGADO')
WORKFLOW_LABELS = {
    'revision': 'Primera revisión',
    'segunda_revision': 'Segunda revisión',
    'seguimiento': 'Gestion y seguimiento',
    'juridica': 'Juridica / tecnica',
}
WORKFLOW_PATHS = {'revision': 'revisor', 'segunda_revision': 'segunda-revision', 'seguimiento': 'seguimiento', 'juridica': 'abogados'}


def authorized_workflows(user_permissions):
    return [key for key in WORKFLOW_LABELS if key in user_permissions]


def workflow_condition(key):
    """Expedientes accionables en la etapa actual, no históricamente asignados."""
    if key == 'revision':
        return and_(Case.current_owner == 'Revisor',
                    Case.current_status != 'ENVIADA_A_SEGUNDA_REVISION')
    if key == 'segunda_revision':
        return or_(Case.current_owner == 'Segunda revisión',
                   Case.current_status == 'ENVIADA_A_SEGUNDA_REVISION')
    if key == 'juridica':
        return or_(Case.current_owner.ilike('Firma Juridica'),
                   Case.current_owner.ilike('Firma Jurídica'),
                   Case.current_owner.ilike('Firma Tecnica'),
                   Case.current_owner.ilike('Firma Técnica'))
    if key == 'seguimiento':
        return Case.current_owner == 'Seguimiento'
    raise ValueError('Tipo de gestión no reconocido.')


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
    query = select(Case).where(
        ~Case.current_status.in_(ARCHIVED),
        Case.needs_reconciliation.is_not(True),
    )
    if scope == 'administracion':
        if user.role != 'admin':
            raise PermissionError('La redistribución corresponde a administración.')
        return query.where(Case.current_owner == 'Administración')
    if scope == 'control':
        if 'seguimiento' not in allowed:
            raise PermissionError('No tiene permiso de control y seguimiento.')
        return query.where(assignment_condition(user, ['seguimiento']))
    conditions = ([workflow_condition(workflow)] if workflow else
                  [workflow_condition(k) for k in allowed])
    query = query.where(or_(*conditions))
    if scope == 'mios':
        return query.where(assignment_condition(user, [workflow] if workflow else allowed))
    if scope != 'equipo':
        raise ValueError('Vista de bandeja no reconocida.')
    return query


def recommended_workflow(case, perms, preferred=''):
    from .assignment_flow import stage_for
    allowed = authorized_workflows(perms)
    if case.needs_reconciliation or case.current_status in ARCHIVED:
        return None
    stage = stage_for(case)
    if stage in allowed and (not preferred or stage == preferred):
        return WORKFLOW_PATHS[stage]
    return None
