package org.flowseal.tgwsproxy

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LatestUpdateRequestTest {
    @Test
    fun olderResultCannotReplaceNewerResultOrDisabledState() {
        val requests = LatestUpdateRequest()
        val older = requests.begin()
        val newer = requests.begin()

        assertFalse(requests.isCurrent(older))
        assertTrue(requests.isCurrent(newer))

        requests.invalidate()
        assertFalse(requests.isCurrent(newer))
    }
}
