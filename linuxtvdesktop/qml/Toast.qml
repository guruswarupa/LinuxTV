import QtQuick 2.15

Item {
    id: root
    height: 48
    opacity: backend.autoLaunchVisible ? 1 : 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 200 } }

    Rectangle {
        anchors.centerIn: parent
        radius: height / 2
        height: 44
        width: row.width + 32
        color: theme.surface

        Row {
            id: row
            anchors.centerIn: parent
            spacing: 16

            Text {
                text: backend.autoLaunchText
                color: theme.text
                font.family: theme.font_family
                font.pixelSize: 14
                anchors.verticalCenter: parent.verticalCenter
            }

            Text {
                text: backend.autoLaunchPaused ? "Resume" : "Back to cancel"
                color: theme.accent
                font.family: theme.font_family
                font.pixelSize: 14
                font.bold: true
                anchors.verticalCenter: parent.verticalCenter

                MouseArea {
                    anchors.fill: parent
                    onClicked: backend.cancelAutoLaunch()
                }
            }
        }
    }
}
