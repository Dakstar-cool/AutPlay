package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*

internal object AccountIntentSchema {
    const val COMPONENT:String="root-intent-materialization-v1"
    val tables:List<AccountTableShape> = listOf(
        AccountTableShape("j_local_intent_order","scope_id,change_id","scope_id,change_id,local_sequence",3,"""CREATE TABLE j_local_intent_order (
            scope_id TEXT NOT NULL,change_id TEXT NOT NULL,local_sequence INTEGER NOT NULL CHECK(local_sequence BETWEEN 1 AND 9007199254740991),
            PRIMARY KEY(scope_id,change_id),UNIQUE(scope_id,local_sequence),FOREIGN KEY(scope_id,change_id) REFERENCES j_local_intent(scope_id,change_id) ON DELETE RESTRICT)"""),
        AccountTableShape("j_account_intent_selection","selection_id","selection_id,source_scope_id,target_scope_id,account_id,review_revision,binding_commit,authority_revision,exact_selection,state",2,"""CREATE TABLE j_account_intent_selection (
            selection_id TEXT PRIMARY KEY,source_scope_id TEXT NOT NULL,target_scope_id TEXT NOT NULL,account_id TEXT NOT NULL,review_revision INTEGER NOT NULL,
            binding_commit TEXT NOT NULL,authority_revision INTEGER NOT NULL,exact_selection BLOB NOT NULL CHECK(length(exact_selection)<=8388608),state TEXT NOT NULL,
            FOREIGN KEY(source_scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT,FOREIGN KEY(target_scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT)"""),
        AccountTableShape("j_account_intent_link","source_scope_id,change_id","source_scope_id,change_id,target_scope_id,account_id,target_id,event_id,lineage_id,selection_id",3,"""CREATE TABLE j_account_intent_link (
            source_scope_id TEXT NOT NULL,change_id TEXT NOT NULL,target_scope_id TEXT NOT NULL,account_id TEXT NOT NULL,target_id TEXT NOT NULL,event_id TEXT NOT NULL,lineage_id TEXT NOT NULL,selection_id TEXT NOT NULL,
            PRIMARY KEY(source_scope_id,change_id),FOREIGN KEY(source_scope_id,change_id) REFERENCES j_local_intent(scope_id,change_id) ON DELETE RESTRICT,
            FOREIGN KEY(target_scope_id,event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT,
            FOREIGN KEY(selection_id) REFERENCES j_account_intent_selection(selection_id) ON DELETE RESTRICT)"""),
        AccountTableShape("j_account_aggregate_map","source_scope_id,aggregate_type,source_id,target_scope_id","source_scope_id,aggregate_type,source_id,target_scope_id,target_id",2,"""CREATE TABLE j_account_aggregate_map (
            source_scope_id TEXT NOT NULL,aggregate_type TEXT NOT NULL,source_id TEXT NOT NULL,target_scope_id TEXT NOT NULL,target_id TEXT NOT NULL,
            PRIMARY KEY(source_scope_id,aggregate_type,source_id,target_scope_id),FOREIGN KEY(source_scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT,
            FOREIGN KEY(target_scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT)"""),
    )
    val create:List<String> = tables.map{it.ddl}+tables.map{"CREATE TRIGGER ${it.name}_retain BEFORE DELETE ON ${it.name} BEGIN SELECT RAISE(ABORT,'ACCOUNT_RETAINED'); END"}+
        tables.filter{it.name!="j_account_intent_selection"}.map{"CREATE TRIGGER ${it.name}_immutable BEFORE UPDATE ON ${it.name} BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END"}
}

/** Private local order only; it is never an authenticated device sequence or wire epoch. */
internal fun retainAccountIntentOrder(sql:AccountPageSql,scope:AccountUuid,change:AccountUuid):Unit {
    val sequence=next(sql.number("SELECT coalesce(max(local_sequence),0) FROM j_local_intent_order WHERE scope_id=?",scope))
    sql.mutate("j_local_intent_order",listOf(scope,change),"INSERT INTO j_local_intent_order(scope_id,change_id,local_sequence) VALUES(?,?,?)",scope,change,sequence)
}
