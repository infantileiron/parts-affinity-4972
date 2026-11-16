import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import MiningRun,AssociationRule
from src.services.analysis import mine,checksum
r=redis.Redis.from_url(os.environ['REDIS_URL'])
with Session() as recovery:
 for pending in recovery.query(MiningRun).filter(MiningRun.state.in_(['queued','running'])).all():pending.state='queued';r.rpush('affinity:runs',pending.id)
 recovery.commit()
while True:
 Path('/tmp/heartbeat').touch()
 try:
  item=r.blpop('affinity:runs',timeout=3)
  if not item:continue
  with Session() as s:
   row=s.get(MiningRun,int(item[1]))
   if not row or row.state!='queued':continue
   snapshot=row.result['snapshot'];row.state='running';s.commit()
   try:
    discovery=[r0 for r0 in snapshot if r0.get('cohort')!='holdout']
    holdout=[r0 for r0 in snapshot if r0.get('cohort')=='holdout']
    result=mine(discovery,row.min_support,row.min_confidence,row.min_lift)
    s.query(AssociationRule).filter_by(run_id=row.id).delete()
    for rule in result['rules']:s.add(AssociationRule(run_id=row.id,**rule))
    row.result={**result,'snapshot':snapshot,'discovery_snapshot':discovery,'holdout_snapshot':holdout,'discovery_checksum':checksum(discovery),'holdout_checksum':checksum(holdout)}
    row.basis_checksum=checksum(snapshot);row.state='done';row.error=''
    s.commit()
    # 仅成功路径发布产物；失败重试不产生重复/覆盖发布
    Path('/artifacts').mkdir(exist_ok=True)
    Path('/artifacts/'+str(row.id)+'.json').write_text(json.dumps(row.result,ensure_ascii=False))
   except Exception as e:
    s.rollback();row=s.get(MiningRun,int(item[1]));row.state='failed';row.error=str(e);s.commit()
 except Exception:logging.exception('worker');time.sleep(2)
