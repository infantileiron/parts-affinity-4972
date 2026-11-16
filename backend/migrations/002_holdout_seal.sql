-- 002 留验分层与封存验收
-- 建组时按设备分层划分发现集/留验集；封存冻结同一版依据；验收重算留验指标
ALTER TABLE `mining_runs`
  ADD COLUMN `cohort_ratio` float DEFAULT 0.3 AFTER `min_lift`,
  ADD COLUMN `validation` json DEFAULT NULL AFTER `result`,
  ADD COLUMN `basis_checksum` varchar(64) DEFAULT NULL AFTER `validation`;

ALTER TABLE `kits`
  ADD COLUMN `source_run_id` int DEFAULT NULL AFTER `rule_id`,
  ADD KEY `source_run_id` (`source_run_id`),
  ADD CONSTRAINT `kits_ibfk_2` FOREIGN KEY (`source_run_id`) REFERENCES `mining_runs` (`id`);

-- mining_runs.state 取值扩展：queued/running/done/failed/sealed/accepted（应用层枚举约束）
