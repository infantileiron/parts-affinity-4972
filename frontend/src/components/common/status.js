import {esc} from '../../utils/html.js';import {STATE_LABEL,VERDICT_LABEL} from '../../constants/enums.js';
export const badge=s=>{const cls=STATE_LABEL[s]?('st-'+s):(VERDICT_LABEL[s]?('vd-'+s):'');return `<span class="tag ${cls}">${esc(STATE_LABEL[s]||VERDICT_LABEL[s]||s)}</span>`};
export const verdictBadge=v=>`<span class="tag vd-${esc(v)}">${esc(VERDICT_LABEL[v]||v||'—')}</span>`;
