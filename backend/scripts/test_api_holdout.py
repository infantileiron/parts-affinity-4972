import os,sys,tempfile
_tmp=tempfile.mkdtemp()
os.environ['DATABASE_URL']='sqlite:///'+_tmp+'/api.db'
os.environ['REDIS_URL']='redis://localhost:6399/0'
os.environ['JWT_SECRET']='test-secret'
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..'))
from fastapi.testclient import TestClient
import src.routes.api as api_mod
from src.main import app
from src.config.database import Session
from src.models.entities import User,Job,MiningRun,AssociationRule
from src.services.analysis import mine,checksum
from src.utils.security import hash_password
from src.constants.enums import PARTS
import random

class FakeRedis:
 def __init__(self):self.q=[]
 def rpush(self,k,v):self.q.append(int(v))
 def blpop(self,k,timeout=0):return None
 def ping(self):return True
fake=FakeRedis()
class _R:
 Redis=type('Redis',(),{'from_url':staticmethod(lambda url:fake)})
 RedisError=Exception
api_mod.redis=_R

from src.config.database import Base,engine
Base.metadata.create_all(engine)

with Session() as s:
 for role in ('admin','analyst','viewer'):
  s.add(User(email=role+'@demo.local',role=role,password=hash_password('Demo123!')))
 rng=random.Random(11)
 for i in range(60):
  items=set(rng.sample(list(PARTS),rng.randint(1,3)))
  if i%2==0:items.update(['轴承','润滑脂'])
  s.add(Job(code='YH-%03d'%i,equipment='泵组',parts=sorted(items),note='x'))
 s.commit()

client=TestClient(app)
fails=[]
def check(name,cond,extra=''):
 print(('PASS' if cond else 'FAIL'),name,extra)
 if not cond:fails.append(name)
def login(role):
 r=client.post('/auth/login',json={'email':role+'@demo.local','password':'Demo123!'});return r.json()['token']
ta=login('analyst');tv=login('viewer');td=login('admin')
H=lambda t:{'Authorization':'Bearer '+t}

def process_queue(force_fail_ids=()):
 # 复刻 worker 取队与计算逻辑（进程内版），保证与 src/worker.py 同口径
 while fake.q:
  rid=fake.q.pop(0)
  with Session() as s:
   row=s.get(MiningRun,rid)
   if not row or row.state!='queued':continue
   snap=row.result['snapshot'];row.state='running';s.commit()
   try:
    if rid in force_fail_ids:raise RuntimeError('模拟计算失败')
    disc=[x for x in snap if x.get('cohort')!='holdout']
    hold=[x for x in snap if x.get('cohort')=='holdout']
    res=mine(disc,row.min_support,row.min_confidence,row.min_lift)
    s.query(AssociationRule).filter_by(run_id=rid).delete()
    for rule in res['rules']:s.add(AssociationRule(run_id=rid,**rule))
    row.result={**res,'snapshot':snap,'discovery_snapshot':disc,'holdout_snapshot':hold,
                'discovery_checksum':checksum(disc),'holdout_checksum':checksum(hold)}
    row.basis_checksum=checksum(snap);row.state='done';row.error=''
   except Exception as e:
    s.rollback();row=s.get(MiningRun,rid);row.state='failed';row.error=str(e)
   s.commit()

# 1. viewer 禁止建组
r=client.post('/runs',headers=H(tv),json={'name':'观察员建组','equipment':'泵组','cohort_ratio':.3})
check('viewer禁止建组',r.status_code==403,str(r.status_code))

# 2. analyst 建组，分层信息返回
r=client.post('/runs',headers=H(ta),json={'name':'11月泵组留验','equipment':'泵组','min_support':.1,'min_confidence':.6,'min_lift':1,'cohort_ratio':.3})
check('建组200',r.status_code==200,str(r.status_code));run=r.json();rid=run['id']
cs=run['result']['cohort_summary']
check('分层摘要',cs['discovery']+cs['holdout']==cs['total'] and cs['holdout']>=2 and cs['discovery']>=2,str(cs))
check('留验比例持久化',abs(run['cohort_ratio']-.3)<1e-9)
snap=run['result']['snapshot']
check('快照带cohort标记',all(x['cohort'] in ('discovery','holdout') for x in snap))
check('两集不相交',len({x['id'] for x in snap if x['cohort']=='discovery'}&{x['id'] for x in snap if x['cohort']=='holdout'})==0)

# 3. 作业修订与挖掘并发：修订后处理队列，快照保持旧版
job_id=snap[0]['id']
r=client.put('/jobs/%d'%job_id,headers=H(ta),json={'code':snap[0]['code'],'equipment':'泵组','parts':['轴承','润滑脂','滤芯','垫片'],'note':'修订'})
check('作业修订成功',r.status_code==200)
process_queue()
r=client.get('/runs/%d'%rid,headers=H(ta));done=r.json()
check('挖掘完成',done['state']=='done',done['state'])
check('快照保留修订前依据',all(not (x['id']==job_id and x['parts']==['轴承','润滑脂','滤芯','垫片']) for x in done['result']['snapshot']))
check('valid_jobs仅发现集',done['result']['valid_jobs']==cs['discovery'])
check('依据checksum已保存',bool(done['basis_checksum']))

# 4. 规则列表 & 封存
r=client.get('/rules?run_id=%d'%rid,headers=H(ta));rules=r.json();check('规则可查询',len(rules)>0,str(len(rules)))
r=client.post('/runs/%d/seal'%rid,headers=H(tv),json={});check('viewer禁止封存',r.status_code==403)
r=client.post('/runs/%d/seal'%rid,headers=H(ta),json={});check('封存200',r.status_code==200 and r.json()['state']=='sealed')
r=client.post('/runs/%d/seal'%rid,headers=H(ta),json={});check('重复封存幂等',r.status_code==200 and r.json()['state']=='sealed')

# 5. 验收
r=client.post('/runs/%d/validate'%rid,headers=H(tv));check('viewer禁止验收',r.status_code==403)
r=client.post('/runs/%d/validate'%rid,headers=H(ta));check('验收200',r.status_code==200)
acc=r.json();check('验收报告结构',acc['validation'] and acc['validation']['verdict'] in ('passed','failed','insufficient_sample'))
check('逐单留验指标',all(set(['count','support','confidence','lift','verdict']).issubset(x) for x in acc['validation']['items']))
check('留验作业数=留验集',acc['validation']['holdout_valid_jobs']==cs['holdout'])
# 再次验收：若已通过则幂等保持 accepted；失败组保持 sealed 可重试
r2=client.post('/runs/%d/validate'%rid,headers=H(ta)).json()
check('重复验收不重复发布/状态稳定',r2['state']==acc['state'])

# 6. 备件包门槛
rule=rules[0]
if acc['state']=='accepted':
 passed=[x for x in acc['validation']['items'] if x['verdict']=='passed']
 check('验收通过组存在passed单项',bool(passed))
 target=next(x for x in rules if x['id']==passed[0]['rule_id'])
 r=client.post('/kits',headers=H(ta),json={'name':'验收通过备件包','rule_id':target['id'],'note':'仅限同规格泵组，核对密封材质与数量'})
 check('通过规则可建包',r.status_code==200,str(r.status_code))
 kit=r.json();check('备件包保留来源批次',kit['source_run_id']==rid)
 not_passed=[x for x in acc['validation']['items'] if x['verdict']!='passed']
 if not_passed:
  bad=next(x for x in rules if x['id']==not_passed[0]['rule_id'])
  r=client.post('/kits',headers=H(ta),json={'name':'不应建成的包','rule_id':bad['id'],'note':'12345678'})
  check('留验未达标/样本不足规则拒绝建包',r.status_code==409,str(r.status_code))
else:
 check('验收未通过状态停留sealed',acc['state']=='sealed')

# 7. 失败重试不重复发布：建组后强制失败一次，再入队重试
r=client.post('/runs',headers=H(ta),json={'name':'失败重试组','equipment':'泵组','cohort_ratio':.3})
rid2=r.json()['id']
process_queue(force_fail_ids=(rid2,))
r=client.get('/runs/%d'%rid2,headers=H(ta));check('失败态记录',r.json()['state']=='failed' and r.json()['error'])
# 模拟恢复后重新入队（worker 重启恢复语义：仅 queued/running 重投，failed 需人工重新入队——这里直接重投验证不重复发布）
fake.q.append(rid2)
with Session() as s:
 s.get(MiningRun,rid2).state='queued';s.commit()
process_queue()
r=client.get('/runs/%d'%rid2,headers=H(ta));check('重试成功',r.json()['state']=='done')
# 重复投递已完成任务应被跳过
fake.q.append(rid2);before=client.get('/runs/%d'%rid2,headers=H(ta)).json()
process_queue()
after=client.get('/runs/%d'%rid2,headers=H(ta)).json()
check('重复投递不重复处理',before['state']==after['state']=='done')
n_rules=len(client.get('/rules?run_id=%d'%rid2,headers=H(ta)).json())
fake.q.append(rid2);process_queue()
check('规则未重复发布',len(client.get('/rules?run_id=%d'%rid2,headers=H(ta)).json())==n_rules)

# 8. 失败/运行中不可封存
r=client.post('/runs/%d/seal'%rid2,headers=H(ta),json={})
check('完成组先封存成功',r.status_code==200)
sealed_rule=client.get('/rules?run_id=%d'%rid2,headers=H(ta)).json()[0]
r=client.post('/kits',headers=H(ta),json={'name':'封存未验收来源包','rule_id':sealed_rule['id'],'note':'12345678'})
check('非accepted批次规则拒绝建包',r.status_code==409,str(r.status_code))
# 9. 导出包含留验依据
r=client.get('/runs/%d/export'%rid,headers=H(ta))
check('导出成功且含留验快照',r.status_code==200 and 'holdout_snapshot' in r.json()['result'])

# 10. 审计记录
r=client.get('/audits',headers=H(td));check('审计含封存验收',r.status_code==200 and any('封存' in a['action'] for a in r.json()) and any('留验验收' in a['action'] for a in r.json()))

# 11. 发现集高分而留验失败：手工分层快照走完整 API
with Session() as s:
 rows=[]
 for i in range(30):
  # 发现集24张全部三联用；留验6张只有前件轴承出现、联用消失（含部分仅轴承的作业，保证可算指标而非样本不足）
  if i<24:parts=['轴承','润滑脂','密封圈'];cohort='discovery'
  elif i<27:parts=['轴承','垫片'];cohort='holdout'
  else:parts=['滤芯','清洗剂'];cohort='holdout'
  rows.append({'id':9000+i,'code':'H-%03d'%i,'equipment':'泵组','parts':parts,'note':'','cohort':cohort})
 disc=[x for x in rows if x['cohort']=='discovery'];hold=[x for x in rows if x['cohort']=='holdout']
 res=mine(disc,.1,.6,1.0)
 run3=MiningRun(name='留验失败演示组',equipment='泵组',min_support=.1,min_confidence=.6,min_lift=1.0,cohort_ratio=.2,
   state='done',basis_checksum=checksum(rows),
   result={'snapshot':rows,'discovery_snapshot':disc,'holdout_snapshot':hold,
           'discovery_checksum':checksum(disc),'holdout_checksum':checksum(hold),**res})
 s.add(run3);s.commit();rid3=run3.id
 for rule in res['rules']:s.add(AssociationRule(run_id=rid3,**rule))
 s.commit()
r=client.post('/runs/%d/seal'%rid3,headers=H(ta),json={});check('失败演示组封存',r.status_code==200)
r=client.post('/runs/%d/validate'%rid3,headers=H(ta));v=r.json()
check('留验验收未达标',r.status_code==200 and v['validation']['verdict']=='failed',str(v['validation']['verdict']))
check('失败后保持封存态可重试',v['state']=='sealed')
check('留验结论含未达标单项',any(x['verdict']=='failed' for x in v['validation']['items']))
check('留验含样本不足单项（前件未出现）',any(x['verdict']=='insufficient_sample' for x in v['validation']['items']))
# 修订竞争：封存后修订作业不影响同一版依据，再次验收结论一致
with Session() as s:
 j0=s.query(Job).filter_by(code='YH-000').first();j0.parts=sorted(set(j0.parts+['叶轮']));s.commit()
r=client.post('/runs/%d/validate'%rid3,headers=H(ta));v2=r.json()
check('修订竞争下依据不变结论稳定',v2['validation']['verdict']=='failed' and v2['result']['snapshot']==v['result']['snapshot'])
# failed 规则禁止建包
bad_rule=client.get('/rules?run_id=%d'%rid3,headers=H(ta)).json()[0]
r=client.post('/kits',headers=H(ta),json={'name':'留验失败来源包','rule_id':bad_rule['id'],'note':'12345678'})
check('留验失败规则拒绝建包',r.status_code==409,str(r.status_code))

print()
print('API RESULT:', 'ALL PASS' if not fails else f'{len(fails)} FAIL -> {fails}')
sys.exit(1 if fails else 0)
