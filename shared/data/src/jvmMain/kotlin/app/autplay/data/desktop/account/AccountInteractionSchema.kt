package app.autplay.data.desktop.account

internal object AccountInteractionSchema {
    const val COMPONENT:String="interaction-projections-v1"
    val tables:List<AccountTableShape> = listOf(
        AccountTableShape("j_user_interaction","scope_id,interaction_id","scope_id,interaction_id,device_id,interaction_type,recommendation_request_id,recording_id,source_rank,presentation_id,display_position,impression_local_id,impression_server_id,feedback_type,exact_payload,source_created_at",2,"""CREATE TABLE j_user_interaction (
            scope_id TEXT NOT NULL,interaction_id TEXT NOT NULL,device_id TEXT,interaction_type TEXT NOT NULL,
            recommendation_request_id TEXT NOT NULL,recording_id TEXT NOT NULL,source_rank INTEGER NOT NULL,presentation_id TEXT,display_position INTEGER,
            impression_local_id TEXT,impression_server_id TEXT,feedback_type TEXT,exact_payload BLOB NOT NULL CHECK(length(exact_payload)<=262144),source_created_at TEXT,
            PRIMARY KEY(scope_id,interaction_id),FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT)"""),
        AccountTableShape("j_presentation_impression","scope_id,presentation_id,request_id,source_rank","scope_id,presentation_id,request_id,source_rank,recording_id,display_position,event_id",3,"""CREATE TABLE j_presentation_impression (
            scope_id TEXT NOT NULL,presentation_id TEXT NOT NULL,request_id TEXT NOT NULL,source_rank INTEGER NOT NULL,recording_id TEXT NOT NULL,display_position INTEGER NOT NULL,event_id TEXT NOT NULL,
            PRIMARY KEY(scope_id,presentation_id,request_id,source_rank),UNIQUE(scope_id,event_id),
            FOREIGN KEY(scope_id,event_id) REFERENCES j_user_interaction(scope_id,interaction_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED)"""),
    )
    val create:List<String> = tables.map{it.ddl}+tables.map{"CREATE TRIGGER ${it.name}_retain BEFORE DELETE ON ${it.name} BEGIN SELECT RAISE(ABORT,'ACCOUNT_RETAINED'); END"}+
        listOf("CREATE TRIGGER j_presentation_impression_immutable BEFORE UPDATE ON j_presentation_impression BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END")
}
