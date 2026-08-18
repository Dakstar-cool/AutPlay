package app.autplay.application.search

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class SafeFtsQueryBuilderTest {
    private val builder = SafeFtsQueryBuilder()

    @Test
    fun preservesCyrillicAndLatinAsQuotedBoundTerms() {
        assertEquals("\"музыка\"* AND \"offline\"*", builder.build("Музыка offline"))
    }

    @Test
    fun hostileOperatorsAndQuotesNeverBecomeFtsSyntax() {
        val query = builder.build("title:secret OR \"x\" NEAR / ../../token")

        assertEquals(
            "\"title\"* AND \"secret\"* AND \"or\"* AND \"x\" AND " +
                "\"near\"* AND \"token\"*",
            query,
        )
        assertFalse(requireNotNull(query).contains(':'))
    }

    @Test
    fun emptyInputDoesNotProduceFullTableScanExpression() {
        assertNull(builder.build("  \"():*  "))
    }

    @Test
    fun tokenCountAndLengthAreBounded() {
        val builder = SafeFtsQueryBuilder(maxInputCharacters = 100, maxTokens = 2, maxTokenCharacters = 4)

        assertEquals("\"abcd\"* AND \"ijkl\"*", builder.build("abcdefgh ijklmnop ignored"))
    }
}
