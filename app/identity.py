"""Identidad estable y conciliación conservadora de asignaciones históricas.

Nunca se reescriben los nombres originales ni se asigna una coincidencia ambigua.
"""
from collections import defaultdict
import re
import unicodedata
from sqlalchemy import delete, select
from .models import User, UserAlias, Case, CaseAssignment, Subtask

WORKFLOWS = ('revision', 'segunda_revision', 'seguimiento', 'juridica')


def identity_key(value):
    value = str(value or '').strip()
    if '@' in value:
        return value.casefold()
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(c for c in value if not unicodedata.combining(c)).casefold()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', value)).strip()


def parse_aliases(text):
    values = [v.strip() for v in re.split(r'[;\n]+', text or '') if v.strip()]
    if len(values) > 12 or any(len(v) > 160 for v in values):
        raise ValueError('Indique hasta 12 nombres alternativos, de máximo 160 caracteres cada uno.')
    aliases = {}
    for v in values:
        key = identity_key(v)
        if len(key) < 2:
            raise ValueError('El nombre alternativo debe tener al menos dos caracteres.')
        aliases[key] = v
    return list(aliases.values())


def _names(db, users):
    index = defaultdict(set)
    for user in users:
        raw = [user.name, user.email]
        parts = identity_key(user.name).split()
        if len(parts) >= 2:
            raw += [' '.join(parts[:n]) for n in range(1, len(parts))]
        for value in raw:
            key = identity_key(value)
            if key:
                index[key].add(user.id)
    for alias in db.scalars(select(UserAlias)).all():
        index[alias.normalized].add(alias.user_id)
    return index


def _candidate_users(text, index):
    key = identity_key(text)
    return index.get(key, set()) if key else set()


def _case_sources(case, tasks):
    from .assignment_flow import stage_for
    stage = stage_for(case)
    # El control es permanente. La revisión se vincula sólo cuando su etapa
    # está activa, nunca por una asignación histórica que ya terminó.
    sources = [('seguimiento', case.followup_assignee)]
    if stage == 'revision':
        sources += [('revision', case.reviewer),
                    ('revision', case.supervisor_name),
                    ('revision', case.supervisor_email)]
    for task in tasks:
        kind = (task.kind or '').upper()
        workflow = ('revision' if kind == 'REVISOR' else
                    'juridica' if kind in ('TECNICA', 'JURIDICA') else 'seguimiento')
        if task.responsible and (workflow == 'seguimiento' or
                                 (stage == workflow and task.active)):
            sources.append((workflow, task.responsible))
    return sources


def rebuild_assignments(db, case_ids=None):
    """Reconstruye vínculos automáticos; preserva vínculos manuales.

    En caso de dos personas con el mismo nombre, revoca la coincidencia automática.
    """
    db.flush()
    users = db.scalars(select(User)).all()
    index = _names(db, users)
    case_query = select(Case)
    if case_ids is not None:
        ids = list(dict.fromkeys(case_ids))
        if not ids:
            return {'assigned': 0, 'ambiguous': 0, 'cases': 0}
        case_query = case_query.where(Case.id_caso.in_(ids))
    cases = db.scalars(case_query).all()
    ids = [c.id_caso for c in cases]
    if not ids:
        return {'assigned': 0, 'ambiguous': 0, 'cases': 0}
    db.execute(delete(CaseAssignment).where(
        CaseAssignment.source == 'auto', CaseAssignment.case_id.in_(ids)
    ))
    manual = set(db.execute(select(
        CaseAssignment.case_id, CaseAssignment.user_id, CaseAssignment.workflow
    ).where(CaseAssignment.case_id.in_(ids))).all())
    tasks = defaultdict(list)
    for task in db.scalars(select(Subtask).where(Subtask.case_id.in_(ids))).all():
        tasks[task.case_id].append(task)
    created = 0
    ambiguous = set()
    used = set(manual)
    for case in cases:
        for workflow, raw_name in _case_sources(case, tasks[case.id_caso]):
            if not raw_name:
                continue
            labels = [raw_name]
            if workflow == 'revision' and raw_name == case.reviewer and (',' in raw_name or ';' in raw_name):
                if not _candidate_users(raw_name, index):
                    labels = [part.strip() for part in re.split(r'[,;]+', raw_name) if part.strip()]
            for label in labels:
                matches = _candidate_users(label, index)
                if len(matches) > 1:
                    ambiguous.add((case.id_caso, workflow, identity_key(label)))
                    continue
                if not matches:
                    continue
                uid = next(iter(matches))
                triple = (case.id_caso, uid, workflow)
                if triple in used:
                    continue
                if any(t[0] == case.id_caso and t[2] == workflow for t in manual):
                    continue
                db.add(CaseAssignment(case_id=case.id_caso, user_id=uid, workflow=workflow,
                                      source='auto', matched_text=str(label)[:250]))
                used.add(triple)
                created += 1
    db.flush()
    return {'assigned': created, 'ambiguous': len(ambiguous), 'cases': len(cases)}


def replace_aliases(db, user, aliases):
    values = parse_aliases(aliases)
    for key in (identity_key(v) for v in values):
        if key == identity_key(user.name) or key == identity_key(user.email):
            continue
        for other in db.scalars(select(User).where(User.id != user.id)).all():
            other_parts = identity_key(other.name).split()
            keys = {identity_key(other.name), identity_key(other.email)}
            if other_parts:
                keys.add(other_parts[0])
                keys.update(' '.join(other_parts[:i]) for i in range(2, len(other_parts)))
            if key in keys:
                raise ValueError('El nombre alternativo coincide con otra cuenta: ' + key)
        collision = db.scalar(select(UserAlias.id).where(
            UserAlias.user_id != user.id, UserAlias.normalized == key))
        if collision:
            raise ValueError('Ese nombre alternativo ya pertenece a otra persona: ' + key)
    db.execute(delete(UserAlias).where(UserAlias.user_id == user.id))
    existing = {identity_key(user.name), identity_key(user.email)}
    for value in values:
        key = identity_key(value)
        if key not in existing:
            db.add(UserAlias(user_id=user.id, value=value, normalized=key))
    db.flush()
    return rebuild_assignments(db)
