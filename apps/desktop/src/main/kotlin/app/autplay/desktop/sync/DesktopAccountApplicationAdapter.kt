package app.autplay.desktop.sync

import app.autplay.application.desktop.account.AccountLease
import app.autplay.desktop.account.DesktopAccountAuthority
import java.util.concurrent.CompletableFuture

/** Compatibility facade only. It owns no projected owner, lease, preparation or K projection port. */
public class DesktopAccountApplicationAdapter(private val authority: DesktopAccountAuthority) {
    public fun currentLease(): CompletableFuture<AccountLease> = authority.currentLease()
    public fun drain(): CompletableFuture<Unit> = authority.drainSettled()
}
