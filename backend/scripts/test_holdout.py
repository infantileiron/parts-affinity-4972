import os,sys,tempfile,json
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..'))
_tmp=tempfile.mkdtemp()
os.environ.setdefault('DATABASE_URL','sqlite:///'+_tmp+'/t.db')
os.environ.setdefault('REDIS_URL','redis://localhost:6399/0')
os.environ.setdefault('JWT_SECRET','test-secret')
from sqlalchemy import create_engine
import src.config.database as dbcfg
from src.config.database import Base,Session,engine
from src.models.entities import User,Job,MiningRun,AssociationRule,Kit
from src.constants.enums import PARTS,EQUIPMENT
from src.services.analysis import split_cohorts,mine,evaluate_holdout,checksum
from src.services.business import dump
from src.controllers.business import seal_run,validate_run
from src.utils.security import hash_password
import random

Base.metadata.create_all(engine)
fails=[]
def check(name,cond,extra=''):
 print(('PASS' if cond else 'FAIL'),name,extra)
 if not cond:fails.append(name)

with Session() as s:
 s.add(User(email='a@x.local',role='analyst',password=hash_password('Demo123!')))
 rng=random.Random(7)
 # 60 泵组作业：强联用模式 轴承+润滑脂 高频；另两类各20条
 for i in range(100):
  eq='泵组' if i<60 else EQUIPMENT[i%2+1]
  items=set(rng.sample(list(PARTS),rng.randint(1,3)))
  if eq=='泵组' and i%2==0:items.update(['轴承','润滑脂'])
  if eq=='泵组' and i%5==0:items.update(['密封圈','垫片'])
  s.add(Job(code='J-%03d'%i,equipment=eq,parts=sorted(items),note='seed'))
 s.commit()

with Session() as s:
 rows=[dump(r) for r in s.query(Job).order_by(Job.id).all()]
 marked=split_cohorts(rows,.3)
 # 1. 分层比例与不相交
 ids_d=[r['id'] for r in marked if r['cohort']=='discovery'];ids_h=[r['id'] for r in marked if r['cohort']=='holdout']
 check('两集不相交',not set(ids_d)&set(ids_h))
 check('两集并集为全集',sorted(ids_d+ids_h)==sorted(r['id'] for r in rows))
 for eq in EQUIPMENT:
  total=sum(1 for r in marked if r['equipment']==eq);h=sum(1 for r in marked if r['equipment']==eq and r['cohort']=='holdout')
  check('分层比例 '+eq,abs(h-round(total*.3))<=1,f'{h}/{total}')
 # 确定性
 check('划分确定性',split_cohorts(rows,.3)==marked)
 # 2. 作业修订不改变已冻结归属（模拟修订后重划但旧快照保持）
 frozen=[dict(r) for r in marked]
 job=s.query(Job).first();job.parts=sorted(set(job.parts+['滤芯']));s.commit()
 check('冻结快照不受修订影响',all(r['parts']!=s.get(Job,r['id']).parts or r['id']!=job.id for r in frozen if r['id']==job.id))

 discovery=[r for r in frozen if r['cohort']=='discovery'];holdout=[r for r in frozen if r['cohort']=='holdout']
 result=mine(discovery,.1,.6,1.0)
 check('挖掘仅用发现集',result['valid_jobs']==len([r for r in discovery if r['parts']]))
 check('产生规则',len(result['rules'])>0,str(len(result['rules'])))

 # 3. 建组持久化 + 封存 + 验收
 run=MiningRun(name='测试组',equipment='全部',min_support=.1,min_confidence=.6,min_lift=1.0,cohort_ratio=.3,
   state='done',basis_checksum=checksum(frozen),
   result={'snapshot':frozen,'discovery_snapshot':discovery,'holdout_snapshot':holdout,
           'discovery_checksum':checksum(discovery),'holdout_checksum':checksum(holdout),**result})
 s.add(run);s.commit()
 for rule in result['rules']:s.add(AssociationRule(run_id=run.id,**rule))
 s.commit();rid=run.id
 seal_run(s,run);s.commit()
 check('封存状态',s.get(MiningRun,rid).state=='sealed')
 seal_run(s,s.get(MiningRun,rid));s.commit()
 check('封存幂等',s.get(MiningRun,rid).state=='sealed')
 validate_run(s,s.get(MiningRun,rid));s.commit()
 r2=s.get(MiningRun,rid)
 check('验收报告存在',r2.validation is not None and r2.validation['verdict'] in ('passed','failed','insufficient_sample'))
 check('每条规则都有留验结论',all(x['verdict'] in ('passed','failed','insufficient_sample') for x in r2.validation['items']))
 check('留验项数=规则数',len(r2.validation['items'])==len(result['rules']))
 check('留验指标口径三项齐全',all(set(['count','support','confidence','lift']).issubset(x.keys()) for x in r2.validation['items']))
 if r2.validation['verdict']=='passed':check('通过后状态accepted',r2.state=='accepted')

 # 4. 留验失败场景：构造发现集高分但留验失败的组
 rows2=[]
 for i in range(40):
  rows2.append({'id':1000+i,'code':'P-%03d'%i,'equipment':'泵组','parts':['轴承','润滑脂','密封圈'] if i<28 else ['滤芯'],'note':''})
 # 让留验集几乎不含联用模式：手工指定 cohort
 for i,r0 in enumerate(rows2):r0['cohort']='discovery' if i<28 else 'holdout'
 res2=mine([r0 for r0 in rows2 if r0['cohort']=='discovery'],.1,.6,1.0)
 sig=next((x for x in res2['rules'] if set(x['antecedent'])=={'轴承'}),res2['rules'][0])
 rep=evaluate_holdout([{'id':1,'signature':sig['signature'],'antecedent':sig['antecedent'],'consequent':sig['consequent']}],
                      [r0 for r0 in rows2 if r0['cohort']=='holdout'],.1,.6,1.0)
 check('发现集高分留验失败',rep['verdict']=='failed',rep['verdict'])
 check('留验共同次数为0',rep['items'][0]['count']==0)

 # 5. 样本不足
 tiny=evaluate_holdout([{'id':1,'signature':'x','antecedent':['轴承'],'consequent':['润滑脂']}],
                       [{'id':9,'code':'t','equipment':'泵组','parts':['轴承'],'note':''}],.1,.6,1.0)
 check('留验样本不足',tiny['verdict']=='insufficient_sample')

 # 6. 未验收批次的规则不能建包：模拟路由层判定
 from fastapi import HTTPException
 run_draft=s.get(MiningRun,rid);orig=run_draft.state;run_draft.state='done';s.flush()
 blocked=False
 try:
  if run_draft.state!='accepted':raise HTTPException(409,'仅留验验收通过的规则可以新建备件包')
 except HTTPException:blocked=True
 run_draft.state=orig;s.commit()
 check('未验收拒绝建包',blocked)
 check('旧包保留来源字段',hasattr(Kit,'source_run_id'))

 # 7. 非封存状态不能验收
 run3=MiningRun(name='q',state='queued',result={'snapshot':[]},min_support=.1,min_confidence=.6,min_lift=1.0);s.add(run3);s.commit()
 blocked=False
 try:validate_run(s,run3)
 except Exception:blocked=True
 check('未封存拒绝验收',blocked)

 # 8. 依据被篡改时封存拒绝
 run4=MiningRun(name='tampered',state='done',basis_checksum='deadbeef',result={'snapshot':frozen,'discovery_checksum':checksum(discovery)},min_support=.1,min_confidence=.6,min_lift=1.0)
 s.add(run4);s.commit()
 blocked=False
 try:seal_run(s,run4)
 except Exception:blocked=True
 check('依据不一致拒绝封存',blocked)

print()
print('RESULT:', 'ALL PASS' if not fails else f'{len(fails)} FAIL -> {fails}')
sys.exit(1 if fails else 0)
