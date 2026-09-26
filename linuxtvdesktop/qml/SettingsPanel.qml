import QtQuick 2.15

Item {
    id: root
    property var items: [
        { key: "network", label: "Network" },
        { key: "bluetooth", label: "Bluetooth" },
        { key: "sound", label: "Sound" },
        { key: "brightness", label: "Brightness" },
        { key: "remote", label: "Remote Control" },
        { key: "autoopen", label: "Auto-Open" },
        { key: "systemupdate", label: "System Update" },
        { key: "appupdate", label: "App Update" },
        { key: "restart", label: "Restart" },
        { key: "shutdown", label: "Shut Down" },
        { key: "reducedfx", label: backend.reducedEffects ? "Reduced Effects: On" : "Reduced Effects: Off" }
    ]
    property int currentIndex: 0
    visible: opacity > 0
    opacity: backend.settingsOpen ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 150 } }

    onVisibleChanged: if (visible) currentIndex = 0

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.5)
        MouseArea { anchors.fill: parent; onClicked: backend.closeSettingsPanel() }
    }

    Rectangle {
        id: sheet
        width: 340
        height: parent.height
        anchors.right: parent.right
        x: backend.settingsOpen ? parent.width - width : parent.width
        color: theme.surface
        Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

        Column {
            anchors.fill: parent
            anchors.margins: 24
            anchors.topMargin: 40
            spacing: 4

            Text {
                text: "Settings"
                color: theme.text
                font.family: theme.font_family
                font.pixelSize: 22
                font.bold: true
                bottomPadding: 16
            }

            Repeater {
                model: root.items
                delegate: Rectangle {
                    width: parent.width
                    height: 48
                    radius: 8
                    color: root.currentIndex === index ? theme.accent : "transparent"

                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 12
                        text: modelData.label
                        color: theme.text
                        font.family: theme.font_family
                        font.pixelSize: 16
                    }

                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            root.currentIndex = index
                            backend.openSetting(modelData.key)
                        }
                    }
                }
            }
        }
    }

    focus: backend.settingsOpen
    Keys.onPressed: (event) => {
        switch (event.key) {
        case Qt.Key_Up:
            root.currentIndex = Math.max(0, root.currentIndex - 1)
            event.accepted = true
            break
        case Qt.Key_Down:
            root.currentIndex = Math.min(root.items.length - 1, root.currentIndex + 1)
            event.accepted = true
            break
        case Qt.Key_Return:
        case Qt.Key_Enter:
            backend.openSetting(root.items[root.currentIndex].key)
            event.accepted = true
            break
        case Qt.Key_Escape:
        case Qt.Key_Back:
            backend.closeSettingsPanel()
            event.accepted = true
            break
        default:
            event.accepted = false
        }
    }
}
