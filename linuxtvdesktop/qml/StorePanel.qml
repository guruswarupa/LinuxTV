import QtQuick 2.15

Column {
    id: root
    spacing: 12

    Text {
        width: parent.width
        text: "Install media apps from the LinuxTV catalog."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }

    Rectangle {
        width: parent.width
        height: Math.min(300, Math.max(60, listView.contentHeight))
        radius: 8
        color: theme.surface_alt
        clip: true

        ListView {
            id: listView
            anchors.fill: parent
            model: backend.panelData.apps || []
            delegate: Rectangle {
                width: listView.width
                height: 56
                color: "transparent"

                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.right: actionBtn.left
                    anchors.rightMargin: 10
                    Text {
                        width: parent.width
                        elide: Text.ElideRight
                        text: modelData.name
                        color: theme.text
                        font.family: theme.font_family
                        font.pixelSize: 14
                        font.bold: true
                    }
                    Text {
                        width: parent.width
                        elide: Text.ElideRight
                        text: modelData.description
                        color: theme.text_muted
                        font.family: theme.font_family
                        font.pixelSize: 12
                    }
                }

                Rectangle {
                    id: actionBtn
                    width: 100
                    height: 34
                    radius: 8
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    color: modelData.installed ? theme.surface : theme.accent
                    opacity: backend.panelBusy ? 0.5 : 1
                    Text {
                        anchors.centerIn: parent
                        text: modelData.installed ? "Installed" : "Install"
                        color: modelData.installed ? theme.text_muted : "white"
                        font.family: theme.font_family
                        font.pixelSize: 13
                        font.bold: !modelData.installed
                    }
                    MouseArea {
                        anchors.fill: parent
                        enabled: !modelData.installed && !backend.panelBusy
                        onClicked: backend.panelAction("install", { id: modelData.id })
                    }
                }
            }
        }
    }

    Text {
        visible: (backend.panelData.apps || []).length === 0
        text: "No apps available."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
    }

    Rectangle {
        width: 90
        height: 38
        radius: 8
        anchors.right: parent.right
        color: theme.surface_alt
        Text { anchors.centerIn: parent; text: "Close"; color: theme.text; font.family: theme.font_family; font.pixelSize: 14 }
        MouseArea { anchors.fill: parent; onClicked: backend.panelAction("cancel", {}) }
    }
}
