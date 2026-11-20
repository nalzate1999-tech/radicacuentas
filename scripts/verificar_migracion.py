"""Valida conteos totales y suma de control del XLSX frente a la copia importada."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from app.database import SessionLocal
from app.models import (ImportBatch,SourceSheet,RawRow,Case,Event,Subtask,
                        RadContract,FormResponse,LegacyLog,ImportIssue)


def main():
    p=argparse.ArgumentParser();p.add_argument('excel');a=p.parse_args()
    h=hashlib.sha256()
    with open(a.excel,'rb') as fp:
        for b in iter(lambda:fp.read(1048576),b''):h.update(b)
    sha=h.hexdigest()
    with SessionLocal() as db:
        batch=db.scalar(select(ImportBatch).where(ImportBatch.sha256==sha))
        if not batch:raise SystemExit('FAIL: el archivo no corresponde a un lote importado.')
        expected=batch.summary['source_rows_by_sheet'];actual={}
        for sh in db.scalars(select(SourceSheet).where(SourceSheet.batch_id==batch.id)):
            n=db.scalar(select(func.count()).select_from(RawRow).where(RawRow.sheet_id==sh.id))
            actual[sh.name]=n
        print('Archivo:',Path(a.excel).name)
        print('SHA256:',sha)
        print('Confrontación por hoja:')
        for name in expected:
            result='OK' if expected[name]==actual.get(name) else 'ERROR'
            print(f'  {result:5} {name}: esperado={expected[name]}, SQL={actual.get(name,0)}')
        for label,model in [('Casos',Case),('Eventos',Event),('Subtareas',Subtask),
                            ('Contratos RAD',RadContract),('Respuestas formulario',FormResponse),
                            ('LOG histórico',LegacyLog),('Novedades conciliación',ImportIssue)]:
            print(label,db.scalar(select(func.count()).select_from(model)))
        if expected!=actual:raise SystemExit('FAIL: existen diferencias de filas.')
        print('OK: integridad de filas crudas verificada. Pendiente: conciliar registros ambiguos.')

if __name__=='__main__':main()
