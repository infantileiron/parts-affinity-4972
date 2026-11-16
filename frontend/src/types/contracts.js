/** @typedef {{id:number,code:string,equipment:string,parts:string[],note:string,cohort?:string}} Job */
/** @typedef {{id:number,name:string,equipment:string,state:string,cohort_ratio:number,basis_checksum:string|null,result:object,validation:null|{verdict:string,holdout_valid_jobs:number,items:Array},error:string}} MiningRun */
/** @typedef {{id:number,run_id:number,signature:string,antecedent:string[],consequent:string[],support:number,confidence:number,lift:number,count:number}} AssociationRule */
/** @typedef {{id:number,name:string,rule_id:number,source_run_id:number|null,parts:string[],note:string,status:string}} Kit */
/** @typedef {{rule_id:number,signature:string,count:number,antecedent_count:number,consequent_count:number,support:number|null,confidence:number|null,lift:number|null,verdict:'passed'|'failed'|'insufficient_sample'}} HoldoutItem */
