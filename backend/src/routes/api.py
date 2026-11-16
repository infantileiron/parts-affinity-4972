import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from src.config.database import db
from src.models.entities import User,Job,MiningRun,AssociationRule,Kit,Audit
from src.schemas.business import Login,JobInput,RunInput,SealInput,KitInput,KitEdit
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.jobs import save_job
from src.services.analysis import split_cohorts
from src.controllers.business import ensure_run,ensure_rule,ensure_kit,seal_run,validate_run
router=APIRouter()
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/jobs')
def jobs(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Job).order_by(Job.id).all()]
@router.post('/jobs')
def create_job(data:JobInput,u=Depends(editor),s=Depends(db)):
 r=save_job(s,data);audit(s,u,'录入作业 '+r['code']);return r
@router.put('/jobs/{i}')
def edit_job(i:int,data:JobInput,u=Depends(editor),s=Depends(db)):
 r=save_job(s,data,i);audit(s,u,'修订作业 '+str(i));return r
@router.get('/runs')
def runs(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(MiningRun).order_by(MiningRun.id.desc()).all()]
@router.post('/runs')
def run(data:RunInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Job).order_by(Job.id).all() if data.equipment=='全部' or r.equipment==data.equipment]
 if len(rows)<4:raise HTTPException(409,'同范围作业不足，无法划分发现集与留验集')
 marked=split_cohorts(rows,data.cohort_ratio)
 holdout=[r for r in marked if r['cohort']=='holdout'];discovery=[r for r in marked if r['cohort']=='discovery']
 if len(holdout)<2:raise HTTPException(409,'留验集至少需要2张作业，请调低留验比例或增加同类作业')
 if len(discovery)<2:raise HTTPException(409,'发现集至少需要2张作业，请调低留验比例')
 payload=data.model_dump()
 r=MiningRun(**payload,result={'snapshot':marked,'cohort_summary':{'total':len(marked),'discovery':len(discovery),'holdout':len(holdout)}});s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('affinity:runs',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请恢复后新建挖掘';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'建组并提交挖掘 '+str(r.id)+'（发现集%d/留验集%d）'%(len(discovery),len(holdout)));return dump(r)
@router.get('/runs/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_run(s,i))
@router.post('/runs/{i}/seal')
def seal(i:int,data:SealInput,u=Depends(editor),s=Depends(db)):
 r=seal_run(s,ensure_run(s,i),data.note);s.commit();audit(s,u,'封存规则批次 '+str(i));return dump(r)
@router.post('/runs/{i}/validate')
def validate(i:int,u=Depends(editor),s=Depends(db)):
 r=validate_run(s,ensure_run(s,i));s.commit();audit(s,u,'留验验收批次 '+str(i)+' 结论 '+str(r.validation['verdict']));return dump(r)
@router.get('/rules')
def rules(run_id:int,u=Depends(current),s=Depends(db)):
 ensure_run(s,run_id);return [dump(r) for r in s.query(AssociationRule).filter_by(run_id=run_id).order_by(AssociationRule.lift.desc()).all()]
@router.get('/runs/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_run(s,i)
 if r.state not in ('done','sealed','accepted'):raise HTTPException(409,'仅成功挖掘可以导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="parts-affinity-{i}.json"'})
@router.get('/kits')
def kits(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Kit).order_by(Kit.id.desc()).all()]
@router.post('/kits')
def kit(data:KitInput,u=Depends(editor),s=Depends(db)):
 rule=ensure_rule(s,data.rule_id);run=ensure_run(s,rule.run_id)
 if run.state!='accepted':raise HTTPException(409,'仅留验验收通过的规则可以新建备件包')
 item=(run.validation or {}).get('items') or []
 verdict=next((x.get('verdict') for x in item if x.get('rule_id')==rule.id),None)
 if verdict!='passed':raise HTTPException(409,'该规则留验结论为%s，不能新建备件包'%verdict)
 r=Kit(**data.model_dump(),source_run_id=run.id,parts=sorted(set(rule.antecedent+rule.consequent)));s.add(r);s.commit();audit(s,u,'起草备件包 '+str(r.id)+'（来源批次 '+str(run.id)+'）');return dump(r)
@router.put('/kits/{i}')
def edit_kit(i:int,data:KitEdit,u=Depends(editor),s=Depends(db)):
 r=ensure_kit(s,i)
 if r.status!='draft':raise HTTPException(409,'已签核备件包不可覆盖，需新建方案')
 r.name=data.name;r.note=data.note;s.commit();audit(s,u,'修改备件包 '+str(i));return dump(r)
@router.post('/kits/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=ensure_kit(s,i)
 if r.status!='draft':raise HTTPException(409,'已签核方案不可重复签核')
 r.status='approved';s.commit();audit(s,u,'签核备件包 '+str(i));return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
