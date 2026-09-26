import QtQuick 2.15

Column {
    id: root
    spacing: 16
    property int value: backend.panelData.value !== undefined ? backend.panelData.value : 50

    Text {
        width: parent.width
        text: "Adjust the brightness level of your display."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }

    Row {
        width: parent.width
        spacing: 12

        Rectangle {
            id: track
            width: parent.width - 62
            height: 8
            radius: 4
            color: theme.surface_alt
            anchors.verticalCenter: parent.verticalCenter

            Rectangle {
                width: track.width * (root.value / 100)
                height: parent.height
                radius: 4
                color: theme.accent
            }

            Rectangle {
                id: handle
                width: 22
                height: 22
                radius: 11
                color: "white"
                y: (track.height - height) / 2
                x: Math.max(0, Math.min(track.width - width, track.width * (root.value / 100) - width / 2))

                MouseArea {
                    anchors.fill: parent
                    anchors.margins: -10
                    drag.target: handle
                    drag.axis: Drag.XAxis
                    drag.minimumX: 0
                    drag.maximumX: track.width - handle.width
                    onPositionChanged: {
                        if (drag.active) {
                            var pct = Math.round((handle.x + handle.width / 2) / track.width * 100)
                            root.value = Math.max(0, Math.min(100, pct))
                            backend.panelAction("set", { value: root.value })
                        }
                    }
                }
            }
        }

        Text {
            text: root.value + "%"
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 15
            font.bold: true
            width: 50
            horizontalAlignment: Text.AlignRight
        }
    }

    Row {
        spacing: 8
        Repeater {
            model: [25, 50, 75, 100]
            delegate: Rectangle {
                width: 60
                height: 34
                radius: 8
                color: theme.surface_alt
                Text { anchors.centerIn: parent; text: modelData + "%"; color: theme.text; font.family: theme.font_family; font.pixelSize: 13 }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        root.value = modelData
                        backend.panelAction("set", { value: modelData })
                    }
                }
            }
        }
    }
}
