from fastapi import HTTPException
from src.models.entities import MiningRun,AssociationRule,Kit
from src.services.analysis import evaluate_holdout,checksum
def ensure_run(s,i):
 r=s.get(MiningRun,i)
 if not r:raise HTTPException(404,'挖掘批次不存在')
 return r
def ensure_rule(s,i):
 r=s.get(AssociationRule,i)
 if not r:raise HTTPException(404,'来源规则不存在')
 return r
def ensure_kit(s,i):
 r=s.get(Kit,i)
 if not r:raise HTTPException(404,'备件包不存在')
 return r
def seal_run(s,run,note=''):
 if run.state in ('queued','running'):raise HTTPException(409,'挖掘尚未完成，不能封存')
 if run.state=='failed':raise HTTPException(409,'挖掘失败的批次不能封存，请调整后新建')
 if run.state in ('sealed','accepted'):return run
 # 封存与作业修订并发：只认建组时冻结的同一版依据
 snapshot=run.result.get('snapshot') or []
 if run.basis_checksum!=checksum(snapshot):raise HTTPException(409,'依据版本已变化，拒绝封存')
 discovery=[r for r in snapshot if r.get('cohort')!='holdout']
 if run.result.get('discovery_checksum')!=checksum(discovery):raise HTTPException(409,'发现集依据与挖掘时不一致，拒绝封存')
 run.state='sealed';run.result={**run.result,'seal_note':note}
 return run
def validate_run(s,run):
 if run.state=='accepted':return run
 if run.state!='sealed':raise HTTPException(409,'仅已封存批次可以留验验收')
 snapshot=run.result.get('snapshot') or []
 if run.basis_checksum!=checksum(snapshot):raise HTTPException(409,'依据版本已变化，拒绝验收')
 rules=s.query(AssociationRule).filter_by(run_id=run.id).order_by(AssociationRule.lift.desc()).all()
 holdout=[r for r in snapshot if r.get('cohort')=='holdout']
 report=evaluate_holdout([{'id':x.id,'signature':x.signature,'antecedent':x.antecedent,'consequent':x.consequent} for x in rules],holdout,run.min_support,run.min_confidence,run.min_lift)
 run.validation=report
 if report['verdict']=='passed':run.state='accepted'
 return run
