package app.autplay.ui.desktop

import javax.xml.stream.XMLInputFactory
import javax.xml.stream.XMLStreamConstants

private const val ANDROID = "http://schemas.android.com/apk/res/android"

private val paths = DesktopGlyph.entries.associateWith { glyph ->
    val factory = XMLInputFactory.newFactory().apply {
        setProperty(XMLInputFactory.SUPPORT_DTD, false)
        setProperty(XMLInputFactory.IS_SUPPORTING_EXTERNAL_ENTITIES, false)
    }
    val name = "/autplay/glyphs/ic_autplay_${glyph.resource}.xml"
    checkNotNull(DesktopGlyph::class.java.getResourceAsStream(name)) { "Missing packaged glyph $name" }.use { stream ->
        val xml = factory.createXMLStreamReader(stream)
        try {
            buildList {
                while (xml.hasNext()) {
                    if (xml.next() == XMLStreamConstants.START_ELEMENT && xml.localName == "path") {
                        val fill = xml.getAttributeValue(ANDROID, "fillColor")
                        add(DesktopGlyphPath(xml.getAttributeValue(ANDROID, "pathData"),
                            fill != null && !fill.contains("transparent") && fill != "#00000000",
                            xml.getAttributeValue(ANDROID, "strokeWidth")?.toFloat() ?: 0f))
                    }
                }
            }
        } finally { xml.close() }
    }
}

internal actual fun desktopGlyphPaths(glyph: DesktopGlyph): List<DesktopGlyphPath> = paths.getValue(glyph)
