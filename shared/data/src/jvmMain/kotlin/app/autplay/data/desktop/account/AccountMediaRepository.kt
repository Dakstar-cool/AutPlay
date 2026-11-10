package app.autplay.data.desktop.account

import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*
import kotlinx.serialization.json.*

/** Typed index only. Physical I/O, live permits and actual LOCAL/K gates belong to the media owner. */
internal class AccountMediaRepository(private val owner:AccountRepositoryOwner):DesktopAccountMedia {
    override suspend fun projection(lease:AccountLease,localTrackId:AccountUuid):JMediaProjectionCapture? = owner.write(lease,
        AccountPortableV1.stableId(lease.scopeId,"media-projection:${localTrackId.value}"),"MEDIA_PROJECTION",bytes("track_id" to localTrackId.value)){sql,_,replay->
        if(!replay)AccountMediaProjectionFence.ensure(sql,lease.storeId,lease.scopeId,localTrackId)
        AccountMediaProjectionFence.capture(sql,lease.storeId,lease.scopeId,localTrackId)
    }
    override suspend fun requestDownload(lease:AccountLease,operationId:AccountUuid,intentId:AccountUuid,localTrackId:AccountUuid,storageClass:String):MediaDownloadIntent {
        require(storageClass in setOf("PINNED","USER_DOWNLOAD"))
        return owner.write(lease,operationId,"MEDIA_REQUEST",bytes("intent_id" to intentId.value,"track_id" to localTrackId.value,"storage_class" to storageClass)){sql,_,replay->
            if(!replay){
                val i=MediaDownloadIntent(record(lease),intentId,localTrackId,null,storageClass,"DOWNLOAD","REQUESTED","AUTO_FOREGROUND",null,0,null,0,null)
                MediaRows.put(sql,"j_download_intent",intentRow(i),insertOnly=true)
            }
            intent(sql,lease,intentId)?:unavailable()
        }
    }
    override suspend fun bindDownload(lease:AccountLease,operationId:AccountUuid,expectedIntentRevision:Long,evidence:MediaSourceEvidence,reservedBytes:Long):MediaDownloadIntent =
        owner.write(lease,operationId,"MEDIA_BIND",evidenceInput(evidence)+bytes("expected" to expectedIntentRevision.toString(),"reserved" to reservedBytes.toString())){sql,witness,replay->
            val id=evidence.intentId?:invalid();val i=intent(sql,lease,id)?:unavailable()
            if(!replay){
                require(i.record.revision==expectedIntentRevision && i.target==null && i.coarseState=="REQUESTED" && i.desiredState=="DOWNLOAD" && evidence.kind==MediaObservationKind.ACQUISITION)
                validateObservation(sql,lease,witness,evidence)
                require(evidence.localTrackRefId==i.localTrackRefId && reservedBytes==evidence.snapshot.totalBytes)
                retain(sql,lease,evidence)
                val s=evidence.snapshot
                MediaRows.put(sql,"j_download_intent",intentRow(i.copy(record=i.record.copy(revision=next(i.record.revision)),
                    target=AccountMediaTarget(s.userTrackRefId,s.userTrackRefRowVersion,s.recordingId,s.audioVariantId,evidence.evidenceId),coarseState="QUEUED",reservedBytes=reservedBytes)))
            }
            intent(sql,lease,id)?:unavailable()
        }
    override suspend fun retainObservation(lease:AccountLease,operationId:AccountUuid,evidence:MediaSourceEvidence):Unit =
        owner.write(lease,operationId,"MEDIA_OBSERVATION",evidenceInput(evidence)){sql,witness,replay->
            if(!replay){
                require(evidence.kind!=MediaObservationKind.ACQUISITION)
                validateObservation(sql,lease,witness,evidence)
                val i=intent(sql,lease,evidence.intentId?:invalid())?:unavailable();val original=readEvidence(sql,lease,i.target?.sourceEvidenceId?:invalid())?:unavailable()
                require(evidence.owner==original.owner && evidence.snapshot.sameContent(original.snapshot) && evidence.snapshot.audioVariantId==original.snapshot.audioVariantId)
                if(evidence.kind==MediaObservationKind.CONTINUATION)require(evidence.snapshot.sameAcquisitionTarget(original.snapshot))
                else require(sameAttachmentTarget(sql,lease,original,evidence))
                retain(sql,lease,evidence)
            }
        }
    override suspend fun readIntent(lease:AccountLease,intentId:AccountUuid,acquisitionScopeId:AccountUuid):MediaDownloadIntent?=owner.read(lease){sql,w->intent(sql,ownedScope(sql,w,lease,acquisitionScopeId),intentId)}
    override suspend fun readIntentPage(lease:AccountLease,after:AccountUuid?,limit:Int):List<MediaDownloadIntent>{
        require(limit in 1..100)
        return owner.read(lease){sql,_->MediaRows.list(sql,"j_download_intent",
            "scope_id=? AND intent_id>? ORDER BY intent_id LIMIT ?",lease.scopeId,after?.value?:"",limit).map(::readIntentRow)}
    }
    override suspend fun readEvidence(lease:AccountLease,evidenceId:AccountUuid,acquisitionScopeId:AccountUuid):MediaSourceEvidence?=owner.read(lease){sql,w->readEvidence(sql,ownedScope(sql,w,lease,acquisitionScopeId),evidenceId)}
    override suspend fun prepareAttempt(lease:AccountLease,operationId:AccountUuid,expectedIntentRevision:Long,previousAttemptLeaseRevision:Long?,attempt:MediaAttempt):MediaAttempt =
        owner.write(lease,operationId,"MEDIA_ATTEMPT_PREPARE",MediaRows.input(attemptRow(attempt))+bytes("expected" to expectedIntentRevision.toString(),"previous_lease" to (previousAttemptLeaseRevision?.toString()?:""))){sql,_,replay->
            if(!replay){
                common(lease,attempt.record);require(attempt.record.revision==1L && attempt.bootId==lease.bootId && attempt.state=="PREPARED")
                val i=intent(sql,lease,attempt.intentId)?:unavailable();val target=i.target?:invalid()
                require(i.record.revision==expectedIntentRevision && i.desiredState=="DOWNLOAD" && i.coarseState in setOf("QUEUED","PAUSED","DOWNLOADING") &&
                    i.continuationMode=="AUTO_FOREGROUND" && attempt.intentRevision==i.record.revision && attempt.attempt==i.currentAttempt+1 && attempt.sourceEvidenceId==target.sourceEvidenceId)
                validateAttempt(attempt);require(attempt.admissionEvidenceId==null && attempt.resourceActivationId==null && attempt.totalBytes==null && attempt.settledBytes==0L)
                require(attempt.sourceGeneration>0 && attempt.attemptLeaseRevision>0 && attempt.operationId==operationId)
                if(i.currentAttempt>0){
                    val old=attempt(sql,lease,i.intentId,i.currentAttempt)?:unavailable()
                    val explicitResume=i.stopReason=="USER" && old.state in setOf("YIELDED","FAILED","CANCELED","UNCERTAIN")
                    require(old.attemptLeaseRevision==previousAttemptLeaseRevision && (explicitResume || old.state=="YIELDED" && old.stopReason=="ATTEMPT_BOUNDARY" && old.bootId==lease.bootId) &&
                        old.prefixId==attempt.prefixId && attempt.attemptLeaseRevision==next(old.attemptLeaseRevision) && attempt.sourceGeneration>old.sourceGeneration &&
                        attempt.startedAtMs >= (i.nextEligibleAtMs?:Long.MAX_VALUE) && attempt.confirmedPrefixBytes==old.confirmedPrefixBytes)
                    if(!explicitResume)require(attempt.replay401Used==old.replay401Used && attempt.reconnectUsed==old.reconnectUsed)
                    else require(!attempt.replay401Used && !attempt.reconnectUsed)
                    val noProgress=if(explicitResume || old.settledBytes>0)0 else old.consecutiveNoProgress+1
                    require(noProgress<=3 && attempt.consecutiveNoProgress==noProgress)
                } else {
                    require(previousAttemptLeaseRevision==null && attempt.confirmedPrefixBytes==0L && attempt.consecutiveNoProgress==0 && !attempt.replay401Used && !attempt.reconnectUsed)
                    MediaRows.put(sql,"j_media_prefix_owner",base(record(lease))+mapOf("prefix_id" to attempt.prefixId,"intent_id" to i.intentId,"source_evidence_id" to target.sourceEvidenceId),true)
                }
                if(sql.number("SELECT count(*) FROM j_media_attempt WHERE state IN ('PREPARED','WAITING','RUNNING','DRAINING')")!=0L)fail(AccountFailure.ACCOUNT_BUSY)
                MediaRows.put(sql,"j_media_attempt",attemptRow(attempt),true)
                MediaRows.put(sql,"j_download_intent",intentRow(i.copy(record=i.record.copy(revision=next(i.record.revision)),currentAttempt=attempt.attempt,
                    coarseState="DOWNLOADING",nextEligibleAtMs=null,stopReason=null)))
            }
            attempt(sql,lease,attempt.intentId,attempt.attempt)?:unavailable()
        }
    override suspend fun updateAttempt(lease:AccountLease,operationId:AccountUuid,expectedRevision:Long,attempt:MediaAttempt):Unit =
        owner.write(lease,operationId,"MEDIA_ATTEMPT_UPDATE",MediaRows.input(attemptRow(attempt))+bytes("expected" to expectedRevision.toString())){sql,_,replay->
            if(!replay){
                common(lease,attempt.record);validateAttempt(attempt)
                val old=attempt(sql,lease,attempt.intentId,attempt.attempt)?:unavailable();val i=intent(sql,lease,attempt.intentId)?:unavailable()
                require(old.record.revision==expectedRevision && attempt.record.revision==next(expectedRevision) && old.bootId==lease.bootId &&
                    i.currentAttempt==attempt.attempt && old.intentRevision==attempt.intentRevision && old.operationId==attempt.operationId &&
                    old.prefixId==attempt.prefixId && old.sourceEvidenceId==attempt.sourceEvidenceId && old.sourceGeneration==attempt.sourceGeneration &&
                    old.attemptLeaseRevision==attempt.attemptLeaseRevision && old.startedAtMs==attempt.startedAtMs && old.deadlineAtMs==attempt.deadlineAtMs &&
                    (!old.replay401Used||attempt.replay401Used) && (!old.reconnectUsed||attempt.reconnectUsed) && old.consecutiveNoProgress==attempt.consecutiveNoProgress &&
                    old.confirmedPrefixBytes==attempt.confirmedPrefixBytes && attempt.settledBytes>=old.settledBytes)
                val transitions=mapOf("PREPARED" to setOf("WAITING","RUNNING","DRAINING","FAILED","CANCELED"),"WAITING" to setOf("WAITING","RUNNING","DRAINING","FAILED","CANCELED"),
                    "RUNNING" to setOf("RUNNING","DRAINING"),"DRAINING" to setOf("YIELDED","DELIVERED","FAILED","CANCELED","UNCERTAIN"))
                require(attempt.state in (transitions[old.state]?:emptySet()))
                if(old.totalBytes!=null)require(old.totalBytes==attempt.totalBytes && old.etag==attempt.etag && old.digestSha256==attempt.digestSha256)
                if(old.resourceActivationId!=null)require(old.resourceActivationId==attempt.resourceActivationId && old.resourceGeneration==attempt.resourceGeneration)
                if(attempt.state=="RUNNING") {
                    require(i.desiredState=="DOWNLOAD" && attempt.admissionEvidenceId!=null && attempt.resourceActivationId!=null && attempt.totalBytes!=null)
                    val fresh=readEvidence(sql,lease,requireNotNull(attempt.admissionEvidenceId))?:unavailable();val original=readEvidence(sql,lease,attempt.sourceEvidenceId)?:unavailable()
                    require(fresh.kind==MediaObservationKind.CONTINUATION && fresh.snapshot.sameAcquisitionTarget(original.snapshot) && fresh.snapshot.sameContent(original.snapshot))
                    require(attempt.etag=="\"sha256-${fresh.snapshot.sha256}\"" && attempt.digestSha256==fresh.snapshot.sha256 && attempt.totalBytes==fresh.snapshot.totalBytes)
                    require(matches(AccountMediaProjectionFence.capture(sql,lease.storeId,lease.scopeId,fresh.localTrackRefId),fresh))
                }
                if(attempt.state=="DELIVERED")require(attempt.totalBytes==attempt.confirmedPrefixBytes && attempt.stopReason=="COMPLETE")
                MediaRows.put(sql,"j_media_attempt",attemptRow(attempt))
                if(attempt.state=="YIELDED"){
                    require(attempt.stopReason=="ATTEMPT_BOUNDARY")
                    val delay=if(attempt.settledBytes>0)1_000L else listOf(5_000L,15_000L,60_000L)[attempt.consecutiveNoProgress.coerceIn(0,2)]
                    MediaRows.put(sql,"j_download_intent",intentRow(i.copy(record=i.record.copy(revision=next(i.record.revision)),coarseState="QUEUED",
                        stopReason="ATTEMPT_BOUNDARY",nextEligibleAtMs=System.currentTimeMillis()+delay)))
                }
            }
        }
    override suspend fun settleAttempt(lease:AccountLease,operationId:AccountUuid,expectedRevision:Long,attempt:MediaAttempt):Unit =
        owner.settle(lease,operationId,"MEDIA_ATTEMPT_SETTLE",MediaRows.input(attemptRow(attempt))+bytes("expected" to expectedRevision.toString())){sql,replay->if(!replay){
            val old=attempt(sql,lease,attempt.intentId,attempt.attempt)?:unavailable()
            common(lease,attempt.record);validateAttempt(attempt)
            require(old.record.revision==expectedRevision && old.bootId==lease.bootId && attempt.record.revision==next(expectedRevision) &&
                old.state in setOf("PREPARED","WAITING","RUNNING","DRAINING") && attempt.state in setOf("DELIVERED","FAILED","CANCELED","UNCERTAIN") &&
                old.copy(record=attempt.record,state=attempt.state,stopReason=attempt.stopReason,settledBytes=attempt.settledBytes)==attempt && attempt.settledBytes>=old.settledBytes)
            if(attempt.state=="DELIVERED")require(old.totalBytes!=null && old.totalBytes==old.confirmedPrefixBytes && attempt.stopReason=="COMPLETE")
            MediaRows.put(sql,"j_media_attempt",attemptRow(attempt))
            val i=intent(sql,lease,attempt.intentId)?:unavailable()
            require(i.currentAttempt==attempt.attempt)
            MediaRows.put(sql,"j_download_intent",intentRow(i.copy(record=i.record.copy(revision=next(i.record.revision)),
                coarseState=if(attempt.state=="CANCELED")"CANCELED" else "PAUSED",continuationMode="USER_REQUIRED",stopReason=attempt.stopReason,nextEligibleAtMs=null)))
        }}
    override suspend fun confirmPrefixBlocks(lease:AccountLease,operationId:AccountUuid,intentId:AccountUuid,attempt:Long,expectedAttemptRevision:Long,blocks:List<MediaPrefixBlock>):Unit {
        require(blocks.size in 1..100)
        val input=blocks.fold(bytes("intent_id" to intentId.value,"attempt" to attempt.toString(),"expected" to expectedAttemptRevision.toString())){b,r->b+MediaRows.input(blockRow(r))}
        owner.write(lease,operationId,"MEDIA_PREFIX",input){sql,_,replay->if(!replay){
            val a=attempt(sql,lease,intentId,attempt)?:unavailable();require(a.record.revision==expectedAttemptRevision && a.bootId==lease.bootId && a.state in setOf("RUNNING","DRAINING"))
            val original=readEvidence(sql,lease,a.sourceEvidenceId)?:unavailable()
            var count=sql.number("SELECT count(*) FROM j_media_prefix_block WHERE scope_id=? AND prefix_id=?",lease.scopeId,a.prefixId).toInt()
            var offset=sql.number("SELECT coalesce(sum(length),0) FROM j_media_prefix_block WHERE scope_id=? AND prefix_id=?",lease.scopeId,a.prefixId)
            require(offset==a.confirmedPrefixBytes)
            for(b in blocks){
                common(lease,b.record);DesktopMediaLimits.digest(b.digestSha256)
                require(b.record.revision==1L && b.prefixId==a.prefixId && b.blockIndex==count && count<8_192 && b.offset==offset && b.length in 1..262_144)
                require(offset+b.length<=original.snapshot.totalBytes && (b.length==262_144 || offset+b.length==original.snapshot.totalBytes))
                MediaRows.put(sql,"j_media_prefix_block",blockRow(b),true);count++;offset+=b.length
            }
            MediaRows.put(sql,"j_media_attempt",attemptRow(a.copy(record=a.record.copy(revision=next(a.record.revision)),confirmedPrefixBytes=offset)))
        }}
    }
    override suspend fun readAttempt(lease:AccountLease,intentId:AccountUuid,attempt:Long):MediaAttempt?=owner.read(lease){sql,_->attempt(sql,lease,intentId,attempt)}
    override suspend fun readPrefixBlocks(lease:AccountLease,prefixId:AccountUuid,afterIndex:Int,limit:Int):List<MediaPrefixBlock>{
        require(afterIndex in -1..8_191 && limit in 1..100)
        return owner.read(lease){sql,_->MediaRows.list(sql,"j_media_prefix_block","scope_id=? AND prefix_id=? AND block_index>? ORDER BY block_index LIMIT ?",lease.scopeId,prefixId,afterIndex,limit).map(::block)}
    }
    override suspend fun setDesiredState(lease:AccountLease,operationId:AccountUuid,intentId:AccountUuid,expectedRevision:Long,desiredState:String):Unit {
        require(desiredState in setOf("DOWNLOAD","CANCEL"))
        owner.write(lease,operationId,"MEDIA_DESIRED",bytes("intent" to intentId.value,"revision" to expectedRevision.toString(),"desired" to desiredState)){sql,_,replay->if(!replay){
            val i=intent(sql,lease,intentId)?:unavailable();require(i.record.revision==expectedRevision && i.coarseState!="COMPLETED")
            MediaRows.put(sql,"j_download_intent",intentRow(i.copy(record=i.record.copy(revision=next(i.record.revision)),desiredState=desiredState,
                continuationMode=if(desiredState=="CANCEL")"USER_REQUIRED" else "AUTO_FOREGROUND",stopReason="USER",
                coarseState=if(desiredState=="DOWNLOAD"){if(i.target==null)"REQUESTED" else "QUEUED"} else i.coarseState,nextEligibleAtMs=if(desiredState=="DOWNLOAD")System.currentTimeMillis() else null)))
        }}
    }
    override suspend fun preparePublication(lease:AccountLease,operationId:AccountUuid,completion:AccountMediaCompletion):Unit {
        val c=completion.copy;val o=completion.ownership;val a=completion.attachment;val p=completion.publication
        listOf(c.record,o.record,a.record,p.record).forEach{common(lease,it);require(it.revision==1L)}
        require(c.copyId==o.copyId && c.copyId==a.copyId && c.copyId==p.copyId && c.publicationId==p.publicationId && o.publicationId==p.publicationId &&
            c.locatorId==p.locatorId && c.completionRevision==p.completionRevision && o.completionRevision==p.completionRevision && c.completionRevision in 1..DesktopAccountLimits.MAX_REVISION &&
            c.integrityState=="VERIFIED" && c.publicationState=="PREPARED" && o.state=="PREPARED" && p.stage=="PREPARED")
        DesktopMediaLimits.digest(c.digestSha256);require(c.totalBytes in 1..DesktopMediaLimits.MAX_OBJECT_BYTES && c.formatSuffix in MediaFormat.entries.map{it.suffix})
        require(c.mediaType.length in 1..128 && c.mediaType.all{it.code in 33..126} && c.storageClass in setOf("USER_DOWNLOAD","PINNED"))
        attachmentShape(a);require(p.attachmentOutcome==a.state && p.expectedProjectionRevision==a.projectionRevision)
        val input=MediaRows.input(copyRow(c))+MediaRows.input(ownershipRow(o))+MediaRows.input(attachmentRow(a))+MediaRows.input(publicationRow(p))
        owner.write(lease,operationId,"MEDIA_PUBLICATION_PREPARE",input){sql,witness,replay->if(!replay){
            require(o.localOwner==localOwner(witness))
            if(o.acquisitionKind=="USER_DOWNLOAD"){
                val i=intent(sql,lease,p.intentId?:invalid())?:unavailable();val target=i.target?:invalid();val original=readEvidence(sql,lease,target.sourceEvidenceId)?:unavailable()
                val attempt=attempt(sql,lease,i.intentId,p.attempt?:invalid())?:unavailable()
                require(i.record.revision==p.expectedIntentRevision && i.desiredState=="DOWNLOAD" && i.currentAttempt==p.attempt && i.coarseState!="COMPLETED" &&
                    p.expectedAttemptLeaseRevision==attempt.attemptLeaseRevision && attempt.state=="DELIVERED" && p.sourceEvidenceId==target.sourceEvidenceId &&
                    c.source==target && c.localTrackRefId==i.localTrackRefId && c.etag=="\"sha256-${original.snapshot.sha256}\"" && c.digestSha256==original.snapshot.sha256 &&
                    c.totalBytes==original.snapshot.totalBytes && o.acquisitionId==i.intentId && o.originLocalVetoRef!=null && p.expectedOriginVetoRevision!=null &&
                    p.materializationRevision==null && p.prefixId==attempt.prefixId && o.localOwner==original.owner)
                require(sql.number("SELECT coalesce(sum(length),0) FROM j_media_prefix_block WHERE scope_id=? AND prefix_id=?",lease.scopeId,attempt.prefixId)==c.totalBytes)
            }else{
                require(o.acquisitionKind=="EXPLICIT_SOURCE_COPY" && c.source==null && c.etag==null && o.originLocalVetoRef==null && p.intentId==null && p.attempt==null &&
                    p.sourceEvidenceId==null && p.expectedIntentRevision==null && p.expectedAttemptLeaseRevision==null && p.expectedOriginVetoRevision==null &&
                    p.prefixId==null && p.materializationRevision!=null && a.state=="DETACHED")
                require(sql.number("SELECT count(*) FROM j_source_selection WHERE selection_id=? AND scope_id=? AND review_revision=? AND state IN ('MATERIALIZED','PARTIAL')",
                    o.acquisitionId,lease.scopeId,p.materializationRevision)==1L)
                val selected=sql.query("""SELECT c.exact_fact FROM j_source_copy c JOIN j_materialization m
                    ON m.source_store_id=c.source_store_id AND m.change_id=c.change_id WHERE c.selection_id=?
                    AND c.kind='TRACK_IMPORTED' AND c.version=1 AND m.scope_id=? AND m.aggregate_id=? LIMIT 2""",
                    o.acquisitionId,lease.scopeId,c.localTrackRefId){AccountSyncWireV1.document(it.getBlob(0)).obj("snapshot")}.singleOrNull()?:invalid()
                require(selected.string("sha256",64)==c.digestSha256 && selected.string("byte_size",20).toLong()==c.totalBytes)
            }
            MediaRows.put(sql,"j_media_copy",copyRow(c),true);MediaRows.put(sql,"j_media_copy_ownership",ownershipRow(o),true)
            MediaRows.put(sql,"j_media_copy_attachment",attachmentRow(a),true);MediaRows.put(sql,"j_media_publication",publicationRow(p),true)
        }}
    }
    override suspend fun commitPublication(lease:AccountLease,operationId:AccountUuid,publicationId:AccountUuid,expectedRevision:Long,currentLocal:AccountLocalCapture,acquisitionScopeId:AccountUuid):AccountMediaCopyView =
        owner.write(lease,operationId,"MEDIA_PUBLICATION_COMMIT",bytes("publication" to publicationId.value,"revision" to expectedRevision.toString(),
            "local_owner_revision" to currentLocal.localGuard.revision.toString(),"origin_revision" to (currentLocal.originLocalGuard?.revision?.toString()?:""),"acquisition_scope" to acquisitionScopeId.value)){sql,witness,replay->
            val acquisition=ownedScope(sql,witness,lease,acquisitionScopeId)
            val p=publication(sql,acquisition,publicationId)?:unavailable();val view=copy(sql,acquisition,p.copyId)?:unavailable()
            if(!replay){
                require(p.record.revision==expectedRevision && p.stage in setOf("PREPARED","LINKED","UNCERTAIN") && p.expectedOwnerRevision==currentLocal.localGuard.revision &&
                    currentLocal.storeId==lease.storeId && currentLocal.scopeId==lease.scopeId && currentLocal.activationRevision==lease.activationRevision &&
                    currentLocal.kAuthorityRevision==lease.authorityRevision && currentLocal.localOwner==view.ownership.localOwner && currentLocal.localOwner==localOwner(witness) &&
                    currentLocal.kOwnerReference==view.ownership.kOwnerRef && currentLocal.localGuard.state==LocalUseState.AVAILABLE && currentLocal.localGuard.veto==LocalMediaVeto.NONE)
                if(view.ownership.acquisitionKind=="USER_DOWNLOAD"){
                    val origin=currentLocal.originLocalGuard?:invalid()
                    require(origin.ownerReference==view.ownership.originLocalVetoRef && origin.revision==p.expectedOriginVetoRevision && origin.state==LocalUseState.AVAILABLE && origin.veto==LocalMediaVeto.NONE)
                    val i=intent(sql,acquisition,p.intentId?:invalid())?:unavailable();val old=attempt(sql,acquisition,i.intentId,p.attempt?:invalid())?:unavailable()
                    require(i.record.revision==p.expectedIntentRevision && i.desiredState=="DOWNLOAD" && i.target==view.copy.source && old.attemptLeaseRevision==p.expectedAttemptLeaseRevision && old.state=="DELIVERED")
                    MediaRows.put(sql,"j_download_intent",intentRow(i.copy(record=i.record.copy(revision=next(i.record.revision)),coarseState="COMPLETED",stopReason="COMPLETE",completedCopyId=view.copy.copyId)))
                }
                val attachment=if(validAttachment(sql,acquisition,view.copy,view.attachment))view.attachment else detached(view.attachment)
                MediaRows.put(sql,"j_media_copy_attachment",attachmentRow(attachment.copy(record=attachment.record.copy(revision=next(attachment.record.revision)))))
                MediaRows.put(sql,"j_media_copy",copyRow(view.copy.copy(record=view.copy.record.copy(revision=next(view.copy.record.revision)),publicationState="COMMITTED")))
                MediaRows.put(sql,"j_media_copy_ownership",ownershipRow(view.ownership.copy(record=view.ownership.record.copy(revision=next(view.ownership.record.revision)),state="OWNED")))
                MediaRows.put(sql,"j_media_publication",publicationRow(p.copy(record=p.record.copy(revision=next(p.record.revision)),attachmentOutcome=attachment.state,
                    expectedProjectionRevision=attachment.projectionRevision,stage="COMMITTED")))
            }
            copy(sql,acquisition,p.copyId)?:unavailable()
        }
    override suspend fun markPublicationUncertain(lease:AccountLease,operationId:AccountUuid,publicationId:AccountUuid,expectedRevision:Long):Unit =
        owner.settle(lease,operationId,"MEDIA_PUBLICATION_UNCERTAIN",bytes("publication" to publicationId.value,"revision" to expectedRevision.toString())){sql,replay->if(!replay){
            val p=publication(sql,lease,publicationId)?:unavailable();require(p.record.revision==expectedRevision && p.stage!="COMMITTED")
            val c=copy(sql,lease,p.copyId)?:unavailable()
            MediaRows.put(sql,"j_media_publication",publicationRow(p.copy(record=p.record.copy(revision=next(p.record.revision)),stage="UNCERTAIN")))
            MediaRows.put(sql,"j_media_copy",copyRow(c.copy.copy(record=c.copy.record.copy(revision=next(c.copy.record.revision)),publicationState="UNCERTAIN")))
            MediaRows.put(sql,"j_media_copy_ownership",ownershipRow(c.ownership.copy(record=c.ownership.record.copy(revision=next(c.ownership.record.revision)),state="SUSPENDED")))
        }}
    override suspend fun readPublication(lease:AccountLease,publicationId:AccountUuid,acquisitionScopeId:AccountUuid):MediaPublication?=owner.read(lease){sql,w->publication(sql,ownedScope(sql,w,lease,acquisitionScopeId),publicationId)}
    override suspend fun readCopy(lease:AccountLease,copyId:AccountUuid,acquisitionScopeId:AccountUuid):AccountMediaCopyView?=owner.read(lease){sql,w->
        copy(sql,ownedScope(sql,w,lease,acquisitionScopeId),copyId)?.also{require(it.ownership.localOwner==localOwner(w))}}
    override suspend fun readOwnedCopies(lease:AccountLease,afterScope:AccountUuid?,afterCopy:AccountUuid?,limit:Int):List<AccountMediaCopyView>{
        require(limit in 1..100 && (afterScope==null)==(afterCopy==null))
        return owner.read(lease){sql,w->
            val account=w.preparation.scope.portableAccountId
            val ids=sql.query("""SELECT c.scope_id,c.copy_id FROM j_media_copy c JOIN j_scope s ON s.scope_id=c.scope_id
                WHERE ((? IS NOT NULL AND s.account_id=?) OR c.scope_id=?) AND (c.scope_id>? OR (c.scope_id=? AND c.copy_id>?))
                ORDER BY c.scope_id,c.copy_id LIMIT ?""",account,account,lease.scopeId,afterScope?.value?:"",afterScope?.value?:"",afterCopy?.value?:"",limit){AccountUuid(it.getText(0)) to AccountUuid(it.getText(1))}
            ids.mapNotNull{(scope,id)->copy(sql,ownedScope(sql,w,lease,scope),id)}.filter{it.ownership.localOwner==localOwner(w)}
        }
    }
    override suspend fun attachCopy(lease:AccountLease,operationId:AccountUuid,copyId:AccountUuid,expectedAttachmentRevision:Long,attachmentEvidenceId:AccountUuid,redirectProjectionRevision:Long?):MediaCopyAttachment =
        owner.write(lease,operationId,"MEDIA_ATTACH",bytes("copy" to copyId.value,"expected" to expectedAttachmentRevision.toString(),"evidence" to attachmentEvidenceId.value,"redirect_revision" to (redirectProjectionRevision?.toString()?:""))){sql,w,replay->
            val c=copy(sql,lease,copyId)?:unavailable()
            if(!replay){
                val e=readEvidence(sql,lease,attachmentEvidenceId)?:unavailable()
                require(c.attachment.record.revision==expectedAttachmentRevision && c.copy.integrityState=="VERIFIED" && c.copy.publicationState=="COMMITTED" &&
                    c.ownership.state=="OWNED" && c.ownership.localOwner==localOwner(w) && e.kind==MediaObservationKind.ATTACHMENT && c.copy.source!=null && e.intentId==c.ownership.acquisitionId)
                val a=MediaCopyAttachment(c.attachment.record.copy(revision=next(expectedAttachmentRevision)),copyId,e.evidenceId,e.localTrackRefId,e.snapshot.userTrackRefId,
                    e.snapshot.userTrackRefRowVersion,e.snapshot.recordingId,e.projectionRevision,redirectProjectionRevision,"ATTACHED")
                require(validAttachment(sql,lease,c.copy,a));MediaRows.put(sql,"j_media_copy_attachment",attachmentRow(a))
            }
            copy(sql,lease,copyId)?.attachment?:unavailable()
        }
    override suspend fun readTrackCopies(lease:AccountLease,trackId:AccountUuid,after:AccountUuid?,limit:Int):List<AccountMediaCopyView>{
        require(limit in 1..100)
        return owner.read(lease){sql,w->
            // One serialized J read prevents a page-to-page writer from inserting behind our cursor.
            // The raw cursor advances even when every retained row in a batch is ineligible.
            val eligible=ArrayList<AccountMediaCopyView>(limit)
            val expectedOwner=localOwner(w)
            var scannedThrough=after?.value?:""
            var uncertain=false
            repeat(100){
                val ids=sql.query("SELECT copy_id FROM j_media_copy_attachment WHERE scope_id=? AND local_track_id=? AND copy_id>? ORDER BY copy_id LIMIT 100",
                    lease.scopeId,trackId,scannedThrough){AccountUuid(it.getText(0))}
                for(id in ids){
                    if(id.value<=scannedThrough)unavailable()
                    scannedThrough=id.value
                    val view=copy(sql,lease,id)
                    if(view==null){uncertain=true;continue}
                    common(lease,view.copy.record);common(lease,view.ownership.record);common(lease,view.attachment.record)
                    if(view.ownership.localOwner!=expectedOwner){uncertain=true;continue}
                    if(view.attachment.state=="DETACHED")continue
                    if(view.attachment.state!="ATTACHED" || view.copy.publicationState!="COMMITTED" ||
                        view.copy.integrityState!="VERIFIED" || view.ownership.state!="OWNED"){
                        uncertain=true;continue
                    }
                    if(!validAttachment(sql,lease,view.copy,view.attachment))continue
                    eligible.add(view)
                    if(eligible.size==limit)return@read eligible
                }
                if(ids.size<100){
                    if(eligible.isEmpty() && uncertain)unavailable()
                    return@read eligible
                }
            }
            // A bounded scan without EOF is not evidence that the track has no LOCAL copy.
            fail(AccountFailure.ACCOUNT_BUSY)
        }
    }
    private fun common(lease:AccountLease,r:AccountMediaRecord):Unit {require(r.storeId==lease.storeId && r.scopeId==lease.scopeId && r.formatVersion==1 && r.revision in 1..DesktopAccountLimits.MAX_REVISION)}
    private fun ownedScope(sql:AccountPageSql,w:AccountProtectedWitness,lease:AccountLease,scope:AccountUuid):AccountLease {
        if(scope==lease.scopeId)return lease
        val account=w.preparation.scope.portableAccountId?:invalid()
        require(sql.number("SELECT count(*) FROM j_scope WHERE scope_id=? AND account_id=?",scope,account)==1L)
        // Query descriptor only: never supplied to admitted(), K or transport as a forged lease.
        return lease.copy(scopeId=scope)
    }
    private fun validateObservation(sql:AccountPageSql,lease:AccountLease,w:AccountProtectedWitness,e:MediaSourceEvidence):Unit {
        val remote=w.preparation.scope.remote?:invalid();val binding=w.preparation.binding?:invalid();val s=e.snapshot
        require(e.storeId==lease.storeId && e.scopeId==lease.scopeId && e.bindingCommitId==lease.bindingCommit && e.owner==localOwner(w) &&
            s.serverInstanceId==remote.serverInstanceId && s.userId==remote.userId && s.deviceId==binding.deviceId && s.identityEpoch==binding.identityEpoch &&
            s.identityThumbprintSha256==binding.serverThumbprint && matches(AccountMediaProjectionFence.capture(sql,lease.storeId,lease.scopeId,e.localTrackRefId),e))
    }
    private fun retain(sql:AccountPageSql,lease:AccountLease,e:MediaSourceEvidence):Unit = MediaRows.put(sql,"j_media_source_evidence",base(record(lease))+ownerRow(e.owner)+mapOf(
        "evidence_id" to e.evidenceId,"intent_id" to (e.intentId?:invalid()),"observation_kind" to e.kind.name,"binding_commit" to e.bindingCommitId,
        "local_track_id" to e.localTrackRefId,"projection_revision" to e.projectionRevision,"utr_version" to e.observedUtrServerRowVersion,
        "recording_id" to e.observedRecordingId,"snapshot_document" to e.snapshot.originalResponseBytes()),true)
    private fun readEvidence(sql:AccountPageSql,lease:AccountLease,id:AccountUuid):MediaSourceEvidence? = MediaRows.one(sql,"j_media_source_evidence",lease.scopeId,"evidence_id" to id)?.let{r->
        MediaSourceEvidence(id,r.id("intent_id"),MediaObservationKind.valueOf(r.text("observation_kind")),r.id("store_id"),r.id("scope_id"),readOwner(r),
            r.id("binding_commit"),r.id("local_track_id"),r.long("projection_revision"),r.long("utr_version"),r.id("recording_id"),MediaSourceSnapshotV1.decode(r.getValue("snapshot_document") as ByteArray))}
    private fun sameAttachmentTarget(sql:AccountPageSql,lease:AccountLease,original:MediaSourceEvidence,fresh:MediaSourceEvidence):Boolean {
        if(original.snapshot.sameAcquisitionTarget(fresh.snapshot))return true
        val a=original.snapshot;val b=fresh.snapshot
        if(!a.sameContent(b)||a.audioVariantId!=b.audioVariantId||a.serverInstanceId!=b.serverInstanceId||a.userId!=b.userId)return false
        val utr=if(a.userTrackRefId==b.userTrackRefId)true else redirect(sql,lease,"USER_TRACK_REF",a.userTrackRefId,b.userTrackRefId)
        val recording=if(a.recordingId==b.recordingId)true else redirect(sql,lease,"RECORDING",a.recordingId,b.recordingId)
        return utr&&recording
    }
    private fun redirect(sql:AccountPageSql,lease:AccountLease,type:String,alias:AccountUuid,target:AccountUuid):Boolean = redirectRevision(sql,lease,type,alias,target)!=null
    private fun redirectRevision(sql:AccountPageSql,lease:AccountLease,type:String,alias:AccountUuid,target:AccountUuid):Long? {
        var current=alias;var revision=0L;val seen=HashSet<AccountUuid>()
        repeat(100){
            if(current==target)return revision.takeIf{it>0}
            if(!seen.add(current))return null
            val row=sql.query("""SELECT r.canonical_server_id,f.projection_revision FROM j_redirect r JOIN j_media_redirect f
                ON f.scope_id=r.scope_id AND f.aggregate_type=r.aggregate_type AND f.alias_id=r.alias_server_id
                WHERE r.scope_id=? AND r.aggregate_type=? AND r.alias_server_id=?""",lease.scopeId,type,current){AccountUuid(it.getText(0)) to it.getLong(1)}.singleOrNull()?:return null
            current=row.first;revision=maxOf(revision,row.second)
        }
        return null
    }
    private fun validAttachment(sql:AccountPageSql,lease:AccountLease,c:MediaCopy,a:MediaCopyAttachment):Boolean {
        val localTrackRefId = a.localTrackRefId ?: return false
        val admissionEvidenceId = a.admissionEvidenceId ?: return false
        if(a.state!="ATTACHED")return false
        val current=AccountMediaProjectionFence.capture(sql,lease.storeId,lease.scopeId,localTrackRefId)?:return false
        val fresh=readEvidence(sql,lease,admissionEvidenceId)?:return false;val original=readEvidence(sql,lease,c.source?.sourceEvidenceId?:return false)?:return false
        if(!matches(current,fresh) || current.projectionRevision!=a.projectionRevision || current.userTrackRefId!=a.userTrackRefId ||
            current.userTrackRefRowVersion!=a.userTrackRefRowVersion || current.recordingId!=a.recordingId || !sameAttachmentTarget(sql,lease,original,fresh))return false
        val revisions=mutableListOf<Long>()
        if(original.snapshot.userTrackRefId!=fresh.snapshot.userTrackRefId)revisions.add(redirectRevision(sql,lease,"USER_TRACK_REF",original.snapshot.userTrackRefId,fresh.snapshot.userTrackRefId)?:return false)
        if(original.snapshot.recordingId!=fresh.snapshot.recordingId)revisions.add(redirectRevision(sql,lease,"RECORDING",original.snapshot.recordingId,fresh.snapshot.recordingId)?:return false)
        return a.redirectProjectionRevision==revisions.maxOrNull()
    }
    private fun validateAttempt(a:MediaAttempt):Unit {
        require(a.attempt in 1..DesktopAccountLimits.MAX_REVISION && a.startedAtMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS &&
            a.deadlineAtMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS && a.deadlineAtMs>a.startedAtMs && a.deadlineAtMs-a.startedAtMs<=900_000 && a.confirmedPrefixBytes in 0..DesktopMediaLimits.MAX_OBJECT_BYTES &&
            a.settledBytes in 0..DesktopMediaLimits.MAX_OBJECT_BYTES && a.consecutiveNoProgress in 0..3 &&
            a.attemptLeaseRevision in 1..DesktopAccountLimits.MAX_REVISION && a.intentRevision in 1..DesktopAccountLimits.MAX_REVISION &&
            a.sourceGeneration in 1..DesktopAccountLimits.MAX_REVISION && (a.resourceGeneration==null || a.resourceGeneration in 1..DesktopAccountLimits.MAX_REVISION))
        require((a.resourceActivationId==null)==(a.resourceGeneration==null) && (a.totalBytes==null)==(a.digestSha256==null) && (a.etag==null)==(a.totalBytes==null))
        a.digestSha256?.let{DesktopMediaLimits.digest(it);require(a.etag=="\"sha256-$it\"")}
        a.totalBytes?.let{require(it in 1..DesktopMediaLimits.MAX_OBJECT_BYTES && a.confirmedPrefixBytes<=it && a.settledBytes<=it)}
        require(a.stopReason==null||a.stopReason in setOf("ATTEMPT_BOUNDARY","USER","SLEEP","QUIT","OWNER","AUTH","NETWORK","STORAGE","UNCERTAIN","ERROR","COMPLETE"))
    }
    private fun attachmentShape(a:MediaCopyAttachment):Unit {
        require(a.state in setOf("ATTACHED","DETACHED"))
        val required=listOf(a.admissionEvidenceId,a.localTrackRefId,a.userTrackRefId,a.userTrackRefRowVersion,a.recordingId,a.projectionRevision)
        require(if(a.state=="DETACHED")required.all{it==null}&&a.redirectProjectionRevision==null else required.all{it!=null})
    }
    private fun intent(sql:AccountPageSql,l:AccountLease,id:AccountUuid):MediaDownloadIntent?=MediaRows.one(sql,"j_download_intent",l.scopeId,"intent_id" to id)?.let(::readIntentRow)
    private fun attempt(sql:AccountPageSql,l:AccountLease,id:AccountUuid,n:Long):MediaAttempt?=MediaRows.one(sql,"j_media_attempt",l.scopeId,"intent_id" to id,"attempt" to n)?.let(::readAttemptRow)
    private fun publication(sql:AccountPageSql,l:AccountLease,id:AccountUuid):MediaPublication?=MediaRows.one(sql,"j_media_publication",l.scopeId,"publication_id" to id)?.let(::readPublicationRow)
    private fun copy(sql:AccountPageSql,l:AccountLease,id:AccountUuid):AccountMediaCopyView? {
        val c=MediaRows.one(sql,"j_media_copy",l.scopeId,"copy_id" to id)?.let(::readCopyRow)?:return null
        val o=MediaRows.one(sql,"j_media_copy_ownership",l.scopeId,"copy_id" to id)?.let(::readOwnershipRow)?:unavailable()
        val a=MediaRows.one(sql,"j_media_copy_attachment",l.scopeId,"copy_id" to id)?.let(::readAttachmentRow)?:unavailable()
        return AccountMediaCopyView(c,o,a)
    }
    private fun evidenceInput(e:MediaSourceEvidence):ByteArray=bytes("evidence" to e.evidenceId.value,"intent" to (e.intentId?.value?:""),"kind" to e.kind.name,
        "binding" to e.bindingCommitId.value,"track" to e.localTrackRefId.value,"projection" to e.projectionRevision.toString())+e.snapshot.originalResponseBytes()
    private fun matches(p:JMediaProjectionCapture?,e:MediaSourceEvidence):Boolean=p!=null && !p.pendingTargetMutation && !p.conflict && !p.tombstone &&
        p.projectionRevision==e.projectionRevision && p.userTrackRefId==e.snapshot.userTrackRefId && p.userTrackRefRowVersion==e.observedUtrServerRowVersion && p.recordingId==e.observedRecordingId
    private fun localOwner(w:AccountProtectedWitness):LocalOwnerKey=w.preparation.scope.portableAccountId?.let{LocalOwnerKey.Portable(it)}?:w.preparation.scope.remote?.let{
        LocalOwnerKey.BoundLocal(it.serverInstanceId,it.userId,w.preparation.bindingCommit)}?:invalid()
    private fun record(l:AccountLease):AccountMediaRecord=AccountMediaRecord(l.storeId,l.scopeId,1)
    private fun invalid():Nothing=fail(AccountFailure.ACCOUNT_INPUT_INVALID)
    private fun unavailable():Nothing=fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
}

/** One connection/transaction and mutation ledger, including invalidation before changed-track reads. */
internal object AccountMediaProjectionFence {
    fun ensure(sql:AccountPageSql,store:AccountUuid,scope:AccountUuid,track:AccountUuid):Unit {
        if(sql.number("SELECT count(*) FROM j_track_ref WHERE scope_id=? AND track_ref_id=?",scope,track)!=1L)return
        if(sql.number("SELECT count(*) FROM j_media_projection WHERE scope_id=? AND track_id=?",scope,track)==0L)
            MediaRows.put(sql,"j_media_projection",base(AccountMediaRecord(store,scope,1))+mapOf("track_id" to track,"projection_revision" to 1L),true)
    }
    fun changed(sql:AccountPageSql,scope:AccountUuid,track:AccountUuid,type:String):Unit {
        if(type!="USER_TRACK_REF")return
        val store=AccountUuid(sql.text("SELECT store_id FROM j_store WHERE singleton=1")?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED))
        ensure(sql,store,scope,track)
        val old=MediaRows.one(sql,"j_media_projection",scope,"track_id" to track)?:return
        MediaRows.put(sql,"j_media_projection",old+mapOf("revision" to next(old.long("revision")),"projection_revision" to next(old.long("projection_revision"))))
        var after=""
        while(true){val rows=MediaRows.list(sql,"j_media_copy_attachment","scope_id=? AND local_track_id=? AND state='ATTACHED' AND copy_id>? ORDER BY copy_id LIMIT 100",scope,track,after)
            if(rows.isEmpty())break
            for(row in rows){val a=readAttachmentRow(row);MediaRows.put(sql,"j_media_copy_attachment",attachmentRow(detached(a).copy(record=a.record.copy(revision=next(a.record.revision)))));after=a.copyId.value}}
    }
    fun redirected(sql:AccountPageSql,scope:AccountUuid,type:String,alias:AccountUuid):Unit {
        val store=AccountUuid(sql.text("SELECT store_id FROM j_store WHERE singleton=1")?:fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED))
        if(MediaRows.one(sql,"j_media_redirect",scope,"aggregate_type" to type,"alias_id" to alias)==null){
            val revision=next(sql.number("SELECT coalesce(max(projection_revision),0) FROM j_media_redirect WHERE scope_id=?",scope))
            MediaRows.put(sql,"j_media_redirect",base(AccountMediaRecord(store,scope,1))+mapOf("aggregate_type" to type,"alias_id" to alias,"projection_revision" to revision),true)}
        if(type=="USER_TRACK_REF")sql.text("SELECT local_id FROM j_projection_state WHERE scope_id=? AND aggregate_type='USER_TRACK_REF' AND server_id=?",scope,alias)?.let{changed(sql,scope,AccountUuid(it),type)}
        if(type=="RECORDING"){var after="";while(true){val ids=sql.query("SELECT track_ref_id FROM j_track_detail WHERE scope_id=? AND recording_id=? AND track_ref_id>? ORDER BY track_ref_id LIMIT 100",scope,alias,after){AccountUuid(it.getText(0))};if(ids.isEmpty())break
            for(id in ids){changed(sql,scope,id,"USER_TRACK_REF");after=id.value}}}
    }
    fun capture(sql:AccountPageSql,store:AccountUuid,scope:AccountUuid,track:AccountUuid):JMediaProjectionCapture?=sql.query("""SELECT p.server_id,p.observed_version,d.recording_id,f.projection_revision,p.pending,p.effective_deleted,
        EXISTS(SELECT 1 FROM j_conflict c WHERE c.scope_id=p.scope_id AND c.aggregate_type=p.aggregate_type AND c.local_id=p.local_id AND c.resolution_state<>'RESOLVED'
            AND NOT EXISTS(SELECT 1 FROM j_conflict_repair r JOIN j_ack a ON a.scope_id=r.scope_id AND a.event_id=r.event_id
                WHERE r.scope_id=c.scope_id AND r.profile_id=c.profile_id AND r.source_id=c.source_id AND r.aggregate_type=c.aggregate_type
                AND r.local_id=c.local_id AND a.terminal=1 AND a.outcome='APPLIED'))
        FROM j_account_projection_visibility p JOIN j_track_detail d ON d.scope_id=p.scope_id AND d.track_ref_id=p.local_id
        JOIN j_media_projection f ON f.scope_id=p.scope_id AND f.track_id=p.local_id WHERE p.scope_id=? AND p.local_id=? AND p.aggregate_type='USER_TRACK_REF'
        AND p.state='COMPLETE' AND p.observed_version IS NOT NULL AND d.recording_id IS NOT NULL AND (p.snapshot_confirmed IS NULL OR p.snapshot_confirmed=1)""",scope,track){
        JMediaProjectionCapture(store,scope,track,AccountUuid(it.getText(0)),it.getLong(3),it.getLong(1),AccountUuid(it.getText(2)),it.getLong(4)==1L,it.getLong(6)==1L,it.getLong(5)==1L)
    }.singleOrNull()
}

private typealias MediaRow=Map<String,Any?>
internal object MediaRows {
    private val shapes=AccountMediaSchema.tables.associateBy{it.name}
    fun put(sql:AccountPageSql,table:String,row:Map<String,Any?>,insertOnly:Boolean=false):Unit {
        val s=shapes.getValue(table);val columns=s.columns.split(',');val keys=s.keys.split(',')
        require(row.keys==columns.toSet())
        val size=row.values.sumOf{when(it){null->1L;is ByteArray->it.size.toLong();is AccountUuid->36L;is String->it.toByteArray(Charsets.UTF_8).size.toLong();else->8L}}
        val cap=when(table){"j_media_source_evidence"->8_192;"j_media_copy_ownership","j_media_copy_attachment","j_media_publication"->2_048;"j_media_prefix_block"->256;else->16_384}
        require(size<=cap)
        if(insertOnly){val scope=row.getValue("scope_id").let{if(it is AccountUuid)it else AccountUuid(it as String)}
            val old=one(sql,table,scope,*keys.filter{it!="scope_id"}.map{it to row[it]}.toTypedArray())
            if(old!=null){require(columns.all{k->equal(old[k],row[k])});return}}
        val suffix=if(insertOnly)"" else " ON CONFLICT(${s.keys}) DO UPDATE SET "+columns.filter{it !in keys}.joinToString(","){"$it=excluded.$it"}
        sql.mutate(table,keys.map{row[it]},"INSERT INTO $table(${s.columns}) VALUES(${columns.joinToString(","){"?"}})$suffix",*columns.map{row[it]}.toTypedArray())
    }
    fun one(sql:AccountPageSql,table:String,scope:AccountUuid,vararg keys:Pair<String,Any?>):Map<String,Any?>? =
        list(sql,table,"scope_id=?"+keys.joinToString(""){" AND ${it.first}=?"},scope,*keys.map{it.second}.toTypedArray()).singleOrNull()
    fun list(sql:AccountPageSql,table:String,where:String,vararg values:Any?):List<Map<String,Any?>> {
        val columns=shapes.getValue(table).columns.split(',')
        return sql.query("SELECT ${columns.joinToString(",")} FROM $table WHERE $where",*values){stmt->columns.mapIndexed{index,k->k to when{
            stmt.isNull(index)->null;k=="snapshot_document"->stmt.getBlob(index);k in AccountMediaSchema.integerColumns->stmt.getLong(index);else->stmt.getText(index)}}.toMap()}
    }
    fun input(row:Map<String,Any?>):ByteArray=AccountPortableV1.canonical(JsonObject(row.mapValues{(_,v)->when(v){null->JsonNull;is AccountUuid->JsonPrimitive(v.value);is Long->JsonPrimitive(v);is Int->JsonPrimitive(v);is String->JsonPrimitive(v);else->fail(AccountFailure.ACCOUNT_INPUT_INVALID)}}))
    private fun equal(a:Any?,b:Any?):Boolean=when{a is ByteArray && b is ByteArray->a.contentEquals(b);b is AccountUuid->a==b.value;a is Number && b is Number->a.toLong()==b.toLong();else->a==b}
}
private fun MediaRow.text(k:String):String=getValue(k) as String
private fun MediaRow.long(k:String):Long=(getValue(k) as Number).toLong()
private fun MediaRow.id(k:String):AccountUuid=AccountUuid(text(k))
private fun MediaRow.nid(k:String):AccountUuid?=get(k)?.let{AccountUuid(it as String)}
private fun MediaRow.nlong(k:String):Long?=(get(k) as? Number)?.toLong()
private fun MediaRow.ntext(k:String):String?=get(k) as? String
private fun base(r:AccountMediaRecord):MediaRow=mapOf("format_version" to r.formatVersion,"store_id" to r.storeId,"scope_id" to r.scopeId,"revision" to r.revision)
private fun readRecord(r:MediaRow):AccountMediaRecord=AccountMediaRecord(r.id("store_id"),r.id("scope_id"),r.long("revision"),r.long("format_version").toInt())
private fun ownerRow(o:LocalOwnerKey):MediaRow=when(o){is LocalOwnerKey.Portable->mapOf("owner_kind" to "PORTABLE","portable_account_id" to o.accountId,"server_instance_id" to null,"server_user_id" to null,"owner_binding_commit" to null)
    is LocalOwnerKey.BoundLocal->mapOf("owner_kind" to "BOUND_LOCAL","portable_account_id" to null,"server_instance_id" to o.serverInstanceId,"server_user_id" to o.userId,"owner_binding_commit" to o.ownerBindingCommitId)}
private fun readOwner(r:MediaRow):LocalOwnerKey=when(r.text("owner_kind")){"PORTABLE"->LocalOwnerKey.Portable(r.id("portable_account_id"));"BOUND_LOCAL"->LocalOwnerKey.BoundLocal(r.id("server_instance_id"),r.id("server_user_id"),r.id("owner_binding_commit"));else->fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)}
private fun targetRow(t:AccountMediaTarget?,prefix:String=""):MediaRow=mapOf("${prefix}user_track_id" to t?.userTrackRefId,"${prefix}utr_version" to t?.userTrackRefRowVersion,"${prefix}recording_id" to t?.recordingId,"${prefix}variant_id" to t?.variantId,"source_evidence_id" to t?.sourceEvidenceId)
private fun readTarget(r:MediaRow,p:String=""):AccountMediaTarget?=r.nid("${p}user_track_id")?.let{AccountMediaTarget(it,r.long("${p}utr_version"),r.id("${p}recording_id"),r.id("${p}variant_id"),r.id("source_evidence_id"))}
private fun intentRow(i:MediaDownloadIntent):MediaRow=base(i.record)+targetRow(i.target)+mapOf("intent_id" to i.intentId,"local_track_id" to i.localTrackRefId,"storage_class" to i.storageClass,"desired_state" to i.desiredState,"coarse_state" to i.coarseState,"continuation_mode" to i.continuationMode,"stop_reason" to i.stopReason,"current_attempt" to i.currentAttempt,"next_eligible_ms" to i.nextEligibleAtMs,"reserved_bytes" to i.reservedBytes,"completed_copy_id" to i.completedCopyId)
private fun readIntentRow(r:MediaRow):MediaDownloadIntent=MediaDownloadIntent(readRecord(r),r.id("intent_id"),r.id("local_track_id"),readTarget(r),r.text("storage_class"),r.text("desired_state"),r.text("coarse_state"),r.text("continuation_mode"),r.ntext("stop_reason"),r.long("current_attempt"),r.nlong("next_eligible_ms"),r.long("reserved_bytes"),r.nid("completed_copy_id"))
private fun copyRow(c:MediaCopy):MediaRow=base(c.record)+targetRow(c.source,"source_")+mapOf("copy_id" to c.copyId,"local_track_id" to c.localTrackRefId,"etag" to c.etag,"digest_sha256" to c.digestSha256,"total_bytes" to c.totalBytes,"media_type" to c.mediaType,"format_suffix" to c.formatSuffix,"storage_class" to c.storageClass,"integrity_state" to c.integrityState,"publication_state" to c.publicationState,"locator_id" to c.locatorId,"completion_revision" to c.completionRevision,"publication_id" to c.publicationId)
private fun readCopyRow(r:MediaRow):MediaCopy=MediaCopy(readRecord(r),r.id("copy_id"),r.id("local_track_id"),readTarget(r,"source_"),r.ntext("etag"),r.text("digest_sha256"),r.long("total_bytes"),r.text("media_type"),r.text("format_suffix"),r.text("storage_class"),r.text("integrity_state"),r.text("publication_state"),r.id("locator_id"),r.long("completion_revision"),r.id("publication_id"))
private fun attachmentRow(a:MediaCopyAttachment):MediaRow=base(a.record)+mapOf("copy_id" to a.copyId,"admission_evidence_id" to a.admissionEvidenceId,"local_track_id" to a.localTrackRefId,"user_track_id" to a.userTrackRefId,"utr_version" to a.userTrackRefRowVersion,"recording_id" to a.recordingId,"projection_revision" to a.projectionRevision,"redirect_projection_revision" to a.redirectProjectionRevision,"state" to a.state)
private fun readAttachmentRow(r:MediaRow):MediaCopyAttachment=MediaCopyAttachment(readRecord(r),r.id("copy_id"),r.nid("admission_evidence_id"),r.nid("local_track_id"),r.nid("user_track_id"),r.nlong("utr_version"),r.nid("recording_id"),r.nlong("projection_revision"),r.nlong("redirect_projection_revision"),r.text("state"))
private fun detached(a:MediaCopyAttachment):MediaCopyAttachment=a.copy(admissionEvidenceId=null,localTrackRefId=null,userTrackRefId=null,userTrackRefRowVersion=null,recordingId=null,projectionRevision=null,redirectProjectionRevision=null,state="DETACHED")
private fun ownershipRow(o:MediaCopyOwnership):MediaRow=base(o.record)+ownerRow(o.localOwner)+mapOf("copy_id" to o.copyId,"k_owner_ref" to o.kOwnerRef,"origin_veto_ref" to o.originLocalVetoRef,"provenance_authority_revision" to o.provenanceKAuthorityRevision,"acquisition_kind" to o.acquisitionKind,"acquisition_id" to o.acquisitionId,"completion_revision" to o.completionRevision,"publication_id" to o.publicationId,"state" to o.state)
private fun readOwnershipRow(r:MediaRow):MediaCopyOwnership=MediaCopyOwnership(readRecord(r),r.id("copy_id"),readOwner(r),r.id("k_owner_ref"),r.nid("origin_veto_ref"),r.long("provenance_authority_revision"),r.text("acquisition_kind"),r.id("acquisition_id"),r.long("completion_revision"),r.id("publication_id"),r.text("state"))
private fun attemptRow(a:MediaAttempt):MediaRow=base(a.record)+mapOf("intent_id" to a.intentId,"attempt" to a.attempt,"boot_id" to a.bootId,"source_generation" to a.sourceGeneration,"intent_revision" to a.intentRevision,"attempt_lease_revision" to a.attemptLeaseRevision,"source_evidence_id" to a.sourceEvidenceId,"admission_evidence_id" to a.admissionEvidenceId,"operation_id" to a.operationId,"resource_activation_id" to a.resourceActivationId,"resource_generation" to a.resourceGeneration,"etag" to a.etag,"digest_sha256" to a.digestSha256,"total_bytes" to a.totalBytes,"prefix_id" to a.prefixId,"confirmed_prefix_bytes" to a.confirmedPrefixBytes,"started_ms" to a.startedAtMs,"deadline_ms" to a.deadlineAtMs,"settled_bytes" to a.settledBytes,"state" to a.state,"stop_reason" to a.stopReason,"replay_401_used" to if(a.replay401Used)1 else 0,"reconnect_used" to if(a.reconnectUsed)1 else 0,"no_progress" to a.consecutiveNoProgress)
private fun readAttemptRow(r:MediaRow):MediaAttempt=MediaAttempt(readRecord(r),r.id("intent_id"),r.long("attempt"),r.id("boot_id"),r.long("source_generation"),r.long("intent_revision"),r.long("attempt_lease_revision"),r.id("source_evidence_id"),r.nid("admission_evidence_id"),r.id("operation_id"),r.nid("resource_activation_id"),r.nlong("resource_generation"),r.ntext("etag"),r.ntext("digest_sha256"),r.nlong("total_bytes"),r.id("prefix_id"),r.long("confirmed_prefix_bytes"),r.long("started_ms"),r.long("deadline_ms"),r.long("settled_bytes"),r.text("state"),r.ntext("stop_reason"),r.long("replay_401_used")==1L,r.long("reconnect_used")==1L,r.long("no_progress").toInt())
private fun blockRow(b:MediaPrefixBlock):MediaRow=base(b.record)+mapOf("prefix_id" to b.prefixId,"block_index" to b.blockIndex,"offset" to b.offset,"length" to b.length,"digest_sha256" to b.digestSha256)
private fun block(r:MediaRow):MediaPrefixBlock=MediaPrefixBlock(readRecord(r),r.id("prefix_id"),r.long("block_index").toInt(),r.long("offset"),r.long("length").toInt(),r.text("digest_sha256"))
private fun publicationRow(p:MediaPublication):MediaRow=base(p.record)+mapOf("publication_id" to p.publicationId,"copy_id" to p.copyId,"intent_id" to p.intentId,"attempt" to p.attempt,"source_evidence_id" to p.sourceEvidenceId,"expected_intent_revision" to p.expectedIntentRevision,"expected_attempt_lease_revision" to p.expectedAttemptLeaseRevision,"expected_owner_revision" to p.expectedOwnerRevision,"expected_origin_veto_revision" to p.expectedOriginVetoRevision,"expected_projection_revision" to p.expectedProjectionRevision,"attachment_outcome" to p.attachmentOutcome,"materialization_revision" to p.materializationRevision,"locator_id" to p.locatorId,"prefix_id" to p.prefixId,"completion_revision" to p.completionRevision,"stage" to p.stage)
private fun readPublicationRow(r:MediaRow):MediaPublication=MediaPublication(readRecord(r),r.id("publication_id"),r.id("copy_id"),r.nid("intent_id"),r.nlong("attempt"),r.nid("source_evidence_id"),r.nlong("expected_intent_revision"),r.nlong("expected_attempt_lease_revision"),r.long("expected_owner_revision"),r.nlong("expected_origin_veto_revision"),r.nlong("expected_projection_revision"),r.text("attachment_outcome"),r.nlong("materialization_revision"),r.id("locator_id"),r.nid("prefix_id"),r.long("completion_revision"),r.text("stage"))
