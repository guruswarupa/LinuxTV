import QtQuick 2.15

Column {
    id: root
    spacing: 12
    property string selectedSink: backend.panelData.currentSink || ""

    Text {
        width: parent.width
        text: "Select the speaker or audio output device."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }

    Rectangle {
        width: parent.width
        height: Math.min(220, Math.max(60, listView.contentHeight))
        radius: 8
        color: theme.surface_alt
        clip: true

        ListView {
            id: listView
            anchors.fill: parent
            model: backend.panelData.sinks || []
            delegate: Rectangle {
                width: listView.width
                height: 44
                color: root.selectedSink === modelData.name ? theme.accent : "transparent"
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    elide: Text.ElideRight
                    text: modelData.label
                    color: theme.text
                    font.family: theme.font_family
                    font.pixelSize: 13
                }
                MouseArea { anchors.fill: parent; onClicked: root.selectedSink = modelData.name }
            }
        }
    }

    Text {
        visible: (backend.panelData.sinks || []).length === 0
        text: "No audio output devices found."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
    }

    Row {
        spacing: 10

        Rectangle {
            width: 110
            height: 36
            radius: 8
            color: theme.surface_alt
            Text { anchors.centerIn: parent; text: "Refresh"; color: theme.text; font.family: theme.font_family; font.pixelSize: 13 }
            MouseArea { anchors.fill: parent; onClicked: backend.panelAction("refresh", {}) }
        }
        Rectangle {
            width: 140
            height: 36
            radius: 8
            color: theme.accent
            opacity: root.selectedSink ? 1 : 0.5
            Text { anchors.centerIn: parent; text: "Set as Default"; color: "white"; font.family: theme.font_family; font.pixelSize: 13; font.bold: true }
            MouseArea {
                anchors.fill: parent
                enabled: root.selectedSink !== ""
                onClicked: backend.panelAction("setDefault", { sink: root.selectedSink })
            }
        }
    }
}
