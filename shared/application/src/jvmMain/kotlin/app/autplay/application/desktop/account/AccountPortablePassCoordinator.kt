package app.autplay.application.desktop.account

import kotlinx.serialization.json.*
import kotlinx.coroutines.sync.Mutex

/**
 * Transport obtains/revalidates transient K credentials outside SQL for each exact lease.
 * Every method must settle and drain its actual exchange before returning or throwing, including
 * cancellation. The host retains the J work claim until this coordinator has returned; lease
 * expiry or coroutine cancellation alone never releases the actual transport owner.
 */
interface AccountPortableDataTransport {
    /** Existing GET /api/v1/portable-accounts/records, six sources, after_time/after_id. <=20 s. */
    suspend fun download(lease:AccountLease,accountId:AccountUuid,source:String,afterTime:String,afterId:AccountUuid):ByteArray
    /** Existing POST body {account_id,records}; must verify the actual accepted-ID response. <=20 s. */
    suspend fun upload(lease:AccountLease,accountId:AccountUuid,records:List<AccountPortableRecord>):Unit
}
enum class AccountPortablePassOutcome { COMPLETE, CONTINUE }

/** One caller owns/drains the actual transport; no timer, worker, second database or detached task. */
class AccountPortablePassCoordinator(private val data:DesktopAccountPortableData,private val transport:AccountPortableDataTransport) {
    private val runLock=Mutex()
    suspend fun run(lease:AccountLease,requestedPassId:AccountUuid):AccountPortablePassOutcome {
        if(!runLock.tryLock())throw AccountStoreException(AccountFailure.ACCOUNT_BUSY)
        try{return runOwned(lease,requestedPassId)}finally{runLock.unlock()}
    }
    private suspend fun runOwned(lease:AccountLease,requestedPassId:AccountUuid):AccountPortablePassOutcome {
        val started=System.nanoTime();var pages=0
        fun available():Boolean=pages<12 && System.nanoTime()-started<60_000_000_000L
        val account=data.ownerAccountId(lease)
        val current=data.cursor(lease,"records")
        val continuing=current.passId!=null && AccountPortableV1.sources.any{!data.cursor(lease,it).complete}
        val pass=if(continuing)current.passId!! else requestedPassId
        data.beginPass(lease,AccountPortableV1.stableId(pass,"begin:v1"),pass)
        while(available()){
            val cursor=data.cursor(lease,"upload")
            if(cursor.complete)break
            val candidates=data.readUploadPage(lease,cursor.uploadedRevision)
            val batch=mutableListOf<AccountPortableUploadRecord>()
            for(record in candidates){
                val proposed=batch+record
                val size=AccountPortableV1.canonical(buildJsonObject{put("account_id",account.value);put("records",JsonArray(proposed.map{it.record.wire()}))}).size
                if(size>DesktopAccountLimits.PORTABLE_PAGE_BYTES)break
                batch.add(record)
            }
            if(candidates.isNotEmpty() && batch.isEmpty())throw AccountStoreException(AccountFailure.ACCOUNT_STORAGE_FULL)
            if(batch.isNotEmpty())transport.upload(lease,account,batch.map{it.record})
            data.recordUpload(lease,AccountUuid.random(),cursor.revision,batch.lastOrNull()?.revision?:cursor.uploadedRevision)
            pages++
        }
        for(source in AccountPortableV1.sources){
            while(available()){
                val cursor=data.cursor(lease,source)
                if(cursor.complete)break
                val bytes=transport.download(lease,account,source,cursor.time?:"1970-01-01T00:00:00Z",
                    cursor.id?:AccountUuid("00000000-0000-0000-0000-000000000000"))
                require(bytes.size in 1..DesktopAccountLimits.PORTABLE_PAGE_BYTES)
                data.applyPortablePage(lease,AccountUuid.random(),source,cursor,bytes)
                pages++
            }
        }
        return if((AccountPortableV1.sources+"upload").all{data.cursor(lease,it).complete})AccountPortablePassOutcome.COMPLETE else AccountPortablePassOutcome.CONTINUE
    }
}
